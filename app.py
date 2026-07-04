from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from finder import ConfigError, geocode_start, get_concerts, require_env

load_dotenv()

BASE_DIR = Path(__file__).parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="concert-placer")


class ConcertRequest(BaseModel):
    artist_url: str
    start_location: str


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
async def config() -> dict:
    """Defaults for prefilling the form. Never exposes the API key."""
    return {
        "artist_url": os.getenv("ARTIST_URL", ""),
        "start_location": os.getenv("START_LOC", ""),
    }


@app.post("/api/concerts")
async def concerts(request: ConcertRequest) -> dict:
    artist_url = request.artist_url.strip()
    start_location = request.start_location.strip()
    if not artist_url or not start_location:
        raise HTTPException(status_code=400, detail="artist_url and start_location are required")

    try:
        api_key = require_env("GOOGLE_MAPS_API_KEY")
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        start = await geocode_start(start_location, api_key)
        found = await get_concerts(artist_url, api_key, start_location)
    except Exception as exc:  # scrape / network failures
        raise HTTPException(status_code=502, detail=f"Lookup failed: {exc}") from exc

    return {
        "start": {"address": start_location, **(start or {})},
        "concerts": [asdict(concert) for concert in found],
    }


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
