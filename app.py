import asyncio
import base64
import binascii
import hmac
import ipaddress
import hashlib
import json
import os
import zlib
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finder import (
    ConfigError,
    TourPageParseError,
    geocode_start,
    get_tour,
    read_json_async,
    require_env,
    resolve_seated_artist_url,
    search_musicbrainz_artists,
)

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
ARTISTS_FILE = Path(os.getenv("ARTISTS_FILE", BASE_DIR / "saved_artists.json"))
IP_GEOLOCATION_URLS = [
    url.strip()
    for url in os.getenv(
        "IP_GEOLOCATION_URLS",
        "https://ipapi.co/{ip_path}json/,https://ipwho.is/{ip}",
    ).split(",")
    if url.strip()
]
IP_GEOLOCATION_TIMEOUT_SECONDS = 5
SHARE_PAYLOAD_VERSION = 1
SHARE_KEY_CONTEXT = b"concert-placer/share-links/v1"
MAX_SHARE_JSON_BYTES = 256 * 1024
MAX_SHARE_COMPRESSED_BYTES = 64 * 1024
MAX_SHARE_TOKEN_CHARS = 12 * 1024
SHARED_DATA_MARKER = "<!-- shared-search-data -->"

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


class ShareLinkError(ValueError):
    pass


def _base64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + padding, altchars=b"-_", validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ShareLinkError("Malformed shared search link.") from exc


def _share_signing_key() -> bytes:
    # A dedicated secret keeps links valid if the Maps key is rotated. The
    # server-only Maps key is a backwards-compatible fallback for existing
    # installs so sharing works without weakening the signature.
    secret = os.getenv("SHARE_LINK_SECRET") or os.getenv("GOOGLE_MAPS_API_KEY")
    if not secret:
        raise ShareLinkError("Set SHARE_LINK_SECRET to enable share links.")
    return hmac.new(secret.encode("utf-8"), SHARE_KEY_CONTEXT, hashlib.sha256).digest()


def _encode_share_token(payload: dict, signing_key: bytes) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(raw) > MAX_SHARE_JSON_BYTES:
        raise ShareLinkError("This search has too much data to fit in a share link.")

    compressed = zlib.compress(raw, level=9)
    if len(compressed) > MAX_SHARE_COMPRESSED_BYTES:
        raise ShareLinkError("This search has too much data to fit in a share link.")

    encoded_payload = _base64url_encode(compressed)
    signature = hmac.new(
        signing_key,
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    token = f"{encoded_payload}.{_base64url_encode(signature)}"
    if len(token) > MAX_SHARE_TOKEN_CHARS:
        raise ShareLinkError("This search has too much data to fit in a share link.")
    return token


def _validate_share_payload(payload: object) -> dict:
    if not isinstance(payload, dict) or payload.get("v") != SHARE_PAYLOAD_VERSION:
        raise ShareLinkError("Unsupported shared search link.")
    if not isinstance(payload.get("artist_url"), str):
        raise ShareLinkError("Shared search is missing its artist URL.")
    if not isinstance(payload.get("start_location"), str):
        raise ShareLinkError("Shared search is missing its start location.")

    result = payload.get("result")
    if not isinstance(result, dict):
        raise ShareLinkError("Shared search is missing its results.")
    if not isinstance(result.get("artist"), dict):
        raise ShareLinkError("Shared search is missing its artist.")
    if not isinstance(result.get("concerts"), list):
        raise ShareLinkError("Shared search has invalid concerts.")
    start = result.get("start")
    if start is not None and not isinstance(start, dict):
        raise ShareLinkError("Shared search has an invalid start location.")
    return payload


def _decode_share_token(token: str, signing_key: bytes) -> dict:
    if len(token) > MAX_SHARE_TOKEN_CHARS:
        raise ShareLinkError("Shared search link is too large.")
    encoded_payload, separator, encoded_signature = token.partition(".")
    if not separator or not encoded_payload or not encoded_signature:
        raise ShareLinkError("Malformed shared search link.")

    signature = _base64url_decode(encoded_signature)
    expected_signature = hmac.new(
        signing_key,
        encoded_payload.encode("ascii"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(signature, expected_signature):
        raise ShareLinkError("This shared search link was modified or is invalid.")

    compressed = _base64url_decode(encoded_payload)
    if len(compressed) > MAX_SHARE_COMPRESSED_BYTES:
        raise ShareLinkError("Shared search link is too large.")

    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(compressed, MAX_SHARE_JSON_BYTES + 1)
    except zlib.error as exc:
        raise ShareLinkError("Shared search data is invalid.") from exc
    if (
        len(raw) > MAX_SHARE_JSON_BYTES
        or decompressor.unconsumed_tail
        or decompressor.unused_data
        or not decompressor.eof
    ):
        raise ShareLinkError("Shared search data is invalid or too large.")

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ShareLinkError("Shared search data is invalid.") from exc
    return _validate_share_payload(payload)


def _shared_index_html(payload: dict) -> str:
    html = (STATIC_DIR / "index.html").read_text("utf-8")
    if SHARED_DATA_MARKER not in html:
        raise RuntimeError("Shared search marker is missing from index.html.")

    # Escaping '<' prevents a result string from closing the data script tag.
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace(
        "<", "\\u003c"
    )
    embedded = (
        '<script id="shared-search-data" type="application/json">'
        f"{serialized}</script>"
    )
    return html.replace(SHARED_DATA_MARKER, embedded, 1)


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


def _public_client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    candidates = [ip.strip() for ip in forwarded_for.split(",") if ip.strip()]
    if request.client and request.client.host:
        candidates.append(request.client.host)

    for candidate in candidates:
        try:
            ip = ipaddress.ip_address(candidate)
        except ValueError:
            continue
        if ip.is_global:
            return str(ip)
    return None


def _format_location_guess(data: dict) -> str | None:
    city = data.get("city")
    region = data.get("region_code") or data.get("region")
    country = data.get("country_name") or data.get("country_code")

    if city and region:
        return f"{city}, {region}"
    if city and country:
        return f"{city}, {country}"
    if region and country:
        return f"{region}, {country}"
    return city or region or country


def _ip_geolocation_urls(ip: str | None) -> list[str]:
    ip_path = f"{ip}/" if ip else ""
    return [
        url.format(ip=ip or "", ip_path=ip_path)
        for url in IP_GEOLOCATION_URLS
    ]


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


@app.get("/share/{token}", response_class=HTMLResponse)
async def shared_search(token: str) -> HTMLResponse:
    try:
        payload = _decode_share_token(token, _share_signing_key())
        html = _shared_index_html(payload)
    except ShareLinkError as exc:
        raise HTTPException(
            status_code=404,
            detail="This shared search link is invalid or has been modified.",
        ) from exc
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.get("/api/config")
async def get_config() -> dict:
    # Prefill values only; the Google Maps key never leaves the server.
    return {
        "artist_url": "",
        "start_location": os.getenv("START_LOC", ""),
    }


@app.get("/api/location-default")
async def get_location_default(request: Request) -> dict:
    ip = _public_client_ip(request)

    for url in _ip_geolocation_urls(ip):
        try:
            data = await asyncio.wait_for(
                read_json_async(url),
                timeout=IP_GEOLOCATION_TIMEOUT_SECONDS,
            )
        except Exception:
            continue

        if data.get("error") or data.get("success") is False:
            continue

        location = _format_location_guess(data)
        if location:
            return {
                "location": location,
                "city": data.get("city"),
                "region": data.get("region_code") or data.get("region"),
                "country": data.get("country_name") or data.get("country_code") or data.get("country"),
                "lat": data.get("latitude"),
                "lng": data.get("longitude"),
            }

    return {
        "location": None,
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
    except TourPageParseError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Lookup failed: {exc}") from exc

    await _upsert_artist(artist_url, tour["artist_name"])

    result = {
        "artist": {"name": tour["artist_name"], "image_url": tour["image_url"]},
        "start": start,
        "concerts": [asdict(concert) for concert in tour["concerts"]],
        "parse_status": tour.get("parse_status", "full"),
        "external_url": tour.get("external_url"),
        "provider": tour.get("provider"),
    }

    share_payload = {
        "v": SHARE_PAYLOAD_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artist_url": artist_url,
        "start_location": start_location,
        "result": result,
    }
    try:
        token = _encode_share_token(share_payload, _share_signing_key())
        result["share_path"] = f"/share/{token}"
    except ShareLinkError as exc:
        result["share_path"] = None
        result["share_error"] = str(exc)
    return result


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
