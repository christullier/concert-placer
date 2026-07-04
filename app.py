import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finder import (
    ConfigError,
    geocode_start,
    get_tour,
    require_env,
    resolve_seated_artist_url,
    search_musicbrainz_artists,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ARTISTS_FILE = BASE_DIR / "saved_artists.json"

app = FastAPI(title="concert-placer")

_artists_lock = asyncio.Lock()


class ConcertRequest(BaseModel):
    artist_url: str
    start_location: str


class ResolveArtistRequest(BaseModel):
    mbid: str


class SaveArtistRequest(BaseModel):
    url: str
    name: str | None = None


def _artist_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]


def _load_artists() -> list[dict]:
    try:
        artists = json.loads(ARTISTS_FILE.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return artists if isinstance(artists, list) else []


def _save_artists(artists: list[dict]) -> None:
    ARTISTS_FILE.write_text(json.dumps(artists, indent=2), "utf-8")


async def _upsert_artist(url: str, name: str | None) -> dict:
    async with _artists_lock:
        artists = _load_artists()
        entry_id = _artist_id(url)
        entry = next((a for a in artists if a.get("id") == entry_id), None)
        if entry is None:
            entry = {"id": entry_id, "url": url}
            artists.append(entry)
        if name:
            entry["name"] = name
        entry.setdefault("name", url)
        entry["last_checked"] = datetime.now(timezone.utc).isoformat()
        _save_artists(artists)
        return entry


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def get_config() -> dict:
    # Prefill values only; the Google Maps key never leaves the server.
    return {
        "artist_url": os.getenv("ARTIST_URL", ""),
        "start_location": os.getenv("START_LOC", ""),
    }


@app.post("/api/concerts")
async def lookup_concerts(request: ConcertRequest) -> dict:
    artist_url = request.artist_url.strip()
    start_location = request.start_location.strip()
    if not artist_url or not start_location:
        raise HTTPException(status_code=400, detail="Artist URL and start location are required.")

    try:
        api_key = require_env("GOOGLE_MAPS_API_KEY")
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        start, tour = await asyncio.gather(
            geocode_start(start_location, api_key),
            get_tour(artist_url, api_key, start_location),
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=422,
            detail="That page doesn't look like a Seated artist page.",
        ) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Lookup failed: {exc}") from exc

    await _upsert_artist(artist_url, tour["artist_name"])

    return {
        "artist": {"name": tour["artist_name"], "image_url": tour["image_url"]},
        "start": start,
        "concerts": [asdict(concert) for concert in tour["concerts"]],
    }


@app.get("/api/artist-search")
async def search_artists(q: str) -> dict:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Artist name is required.")

    try:
        artists = await search_musicbrainz_artists(query)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MusicBrainz search failed: {exc}") from exc

    return {"artists": artists}


@app.post("/api/resolve-artist")
async def resolve_artist(request: ResolveArtistRequest) -> dict:
    mbid = request.mbid.strip()
    if not mbid:
        raise HTTPException(status_code=400, detail="MusicBrainz artist ID is required.")

    try:
        return await resolve_seated_artist_url(mbid, max_urls=3)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Artist URL resolution failed: {exc}") from exc


@app.get("/api/artists")
async def list_artists() -> dict:
    async with _artists_lock:
        artists = _load_artists()
    artists.sort(key=lambda a: a.get("last_checked", ""), reverse=True)
    return {"artists": artists}


@app.post("/api/artists")
async def save_artist(request: SaveArtistRequest) -> dict:
    url = request.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Artist URL is required.")
    return await _upsert_artist(url, request.name)


@app.delete("/api/artists/{artist_id}", status_code=204)
async def delete_artist(artist_id: str) -> Response:
    async with _artists_lock:
        artists = _load_artists()
        remaining = [a for a in artists if a.get("id") != artist_id]
        if len(remaining) == len(artists):
            raise HTTPException(status_code=404, detail="Unknown artist.")
        _save_artists(remaining)
    return Response(status_code=204)


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app:app", host=host, port=port)
