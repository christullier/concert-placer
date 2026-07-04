import asyncio
import json
import os
import re
import time
from urllib.parse import urlparse
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from Concert import Concert

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
SEATED_TOUR_URL = "https://cdn.seated.com/api/tour/{artist_id}"
MUSICBRAINZ_ARTIST_URL = "https://musicbrainz.org/ws/2/artist"
METERS_PER_MILE = 1609.344
REQUEST_TIMEOUT_SECONDS = 30
ARTIST_PAGE_PROBE_TIMEOUT_SECONDS = 8
# Seated's CDN and some artist sites reject the default urllib User-Agent.
USER_AGENT = "Mozilla/5.0 (compatible; concert-placer/1.0)"
MUSICBRAINZ_USER_AGENT = os.getenv(
    "MUSICBRAINZ_USER_AGENT",
    "concert-placer/1.0 (https://github.com/christullier/concert-placer)",
)

_musicbrainz_lock = asyncio.Lock()
_last_musicbrainz_request = 0.0


class ConfigError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def build_url(base_url: str, params: dict[str, str]) -> str:
    return f"{base_url}?{urlencode(params)}"


def read_url(
    url: str,
    *,
    user_agent: str = USER_AGENT,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> str:
    request = Request(url, headers={"User-Agent": user_agent})
    with urlopen(request, timeout=timeout) as page:
        return page.read().decode("utf-8")


def read_json(url: str, *, user_agent: str = USER_AGENT) -> dict:
    return json.loads(read_url(url, user_agent=user_agent))


async def read_json_async(url: str, *, user_agent: str = USER_AGENT) -> dict:
    return await asyncio.to_thread(read_json, url, user_agent=user_agent)


async def read_musicbrainz_json_async(url: str) -> dict:
    global _last_musicbrainz_request

    async with _musicbrainz_lock:
        elapsed = time.monotonic() - _last_musicbrainz_request
        if elapsed < 1:
            await asyncio.sleep(1 - elapsed)
        try:
            return await read_json_async(url, user_agent=MUSICBRAINZ_USER_AGENT)
        finally:
            _last_musicbrainz_request = time.monotonic()


def get_artist_id_from_html(html: str) -> str:
    match = re.search(r'artist-id="([^"]+)"', html)
    if not match:
        raise RuntimeError("Could not find artist-id in the artist page.")
    return match.group(1)


def get_artist_id(url: str, *, timeout: int = REQUEST_TIMEOUT_SECONDS) -> str:
    return get_artist_id_from_html(read_url(url, timeout=timeout))


def detect_tour_provider(html: str) -> str | None:
    lowered = html.lower()
    if re.search(r'artist-id="[^"]+"', html):
        return "seated"
    if (
        "squarespace-events-collection" in lowered or "squarespace-tourdates" in lowered
    ) and not any(
        marker in lowered
        for marker in ("bandsintown", "songkick", "seated.com", "eventbrite", "dice.fm")
    ):
        return "squarespace-events"
    if "bandsintown" in lowered:
        return "bandsintown"
    if "widget.songkick.com" in lowered or "songkick-widget" in lowered:
        return "songkick"
    if "eventbrite" in lowered:
        return "eventbrite"
    if "widgets.dice" in lowered or "dice.fm" in lowered:
        return "dice"
    if "ticketmaster.com" in lowered or "livenation.com" in lowered:
        return "ticketmaster"
    if "axs.com" in lowered:
        return "axs"
    return None


async def search_musicbrainz_artists(query: str, limit: int = 8) -> list[dict]:
    url = build_url(
        MUSICBRAINZ_ARTIST_URL,
        {"query": query, "fmt": "json", "limit": str(limit)},
    )
    musicbrainz_json = await read_musicbrainz_json_async(url)
    artists = musicbrainz_json.get("artists", [])
    return [format_musicbrainz_artist(artist) for artist in artists if artist.get("id")]


def format_musicbrainz_artist(artist: dict) -> dict:
    area = artist.get("area") or {}
    return {
        "mbid": artist.get("id"),
        "name": artist.get("name"),
        "sort_name": artist.get("sort-name"),
        "disambiguation": artist.get("disambiguation"),
        "country": artist.get("country"),
        "area": area.get("name"),
        "type": artist.get("type"),
        "score": artist.get("score"),
    }


async def get_musicbrainz_artist_urls(mbid: str) -> list[dict]:
    url = build_url(
        f"{MUSICBRAINZ_ARTIST_URL}/{mbid}",
        {"inc": "url-rels", "fmt": "json"},
    )
    artist_json = await read_musicbrainz_json_async(url)
    return ranked_artist_urls(artist_json.get("relations", []))


def ranked_artist_urls(relations: list[dict]) -> list[dict]:
    candidates = []
    seen = set()
    for relation in relations:
        url = ((relation.get("url") or {}).get("resource") or "").strip()
        if not url or url in seen:
            continue
        if not is_useful_artist_url(url):
            continue
        seen.add(url)
        candidates.append(
            {
                "url": url,
                "type": relation.get("type"),
                "score": artist_url_priority(relation, url),
            }
        )

    candidates.sort(key=lambda candidate: (candidate["score"], candidate["url"]))
    for candidate in candidates:
        candidate.pop("score", None)
    return candidates


def is_useful_artist_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    excluded_hosts = (
        "allmusic.com",
        "deezer.com",
        "discogs.com",
        "genius.com",
        "imdb.com",
        "last.fm",
        "music.apple.com",
        "musicbrainz.org",
        "secondhandsongs.com",
        "setlist.fm",
        "songkick.com",
        "spotify.com",
        "tidal.com",
        "wikidata.org",
        "wikipedia.org",
        "youtube.com",
        "youtu.be",
    )
    return not any(host == blocked or host.endswith(f".{blocked}") for blocked in excluded_hosts)


def artist_url_priority(relation: dict, url: str) -> int:
    relation_type = (relation.get("type") or "").lower()
    host = urlparse(url).netloc.lower()

    if relation_type == "official homepage":
        return 0
    if "official" in relation_type:
        return 10
    if "bandcamp" in host:
        return 30
    if "soundcloud" in host:
        return 40
    if relation_type == "social network":
        return 50
    return 80


async def resolve_seated_artist_url(mbid: str, max_urls: int = 3) -> dict:
    candidates = await get_musicbrainz_artist_urls(mbid)
    tried_urls = []

    for candidate in candidates[:max_urls]:
        url = candidate["url"]
        tried_urls.append(candidate)
        try:
            artist_id = await asyncio.to_thread(
                get_artist_id,
                url,
                timeout=ARTIST_PAGE_PROBE_TIMEOUT_SECONDS,
            )
        except Exception:
            continue
        return {
            "artist_url": url,
            "source_url": url,
            "seated_artist_id": artist_id,
            "tried_urls": tried_urls,
            "candidates": candidates[:max_urls],
        }

    return {
        "artist_url": None,
        "source_url": None,
        "seated_artist_id": None,
        "tried_urls": tried_urls,
        "candidates": candidates[:max_urls],
    }


async def get_tour_info(artist_id: str) -> dict:
    url = build_url(SEATED_TOUR_URL.format(artist_id=artist_id), {"include": "tour-events"})
    return await read_json_async(url)


async def geocode(query: str, api_key: str) -> dict | None:
    url = build_url(GEOCODE_URL, {"address": query, "key": api_key})
    maps_json = await read_json_async(url)

    if maps_json.get("status") != "OK" or not maps_json.get("results"):
        return None

    result = maps_json["results"][0]
    location = result["geometry"]["location"]
    return {
        "address": result["formatted_address"],
        "lat": location["lat"],
        "lng": location["lng"],
    }


async def geocode_start(start_location: str, api_key: str) -> dict | None:
    return await geocode(start_location, api_key)


async def get_address(concert: Concert, api_key: str) -> str | None:
    query = f"{concert.venue}, {concert.city}"
    url = build_url(GEOCODE_URL, {"address": query, "key": api_key})
    maps_json = await read_json_async(url)

    if maps_json.get("status") != "OK" or not maps_json.get("results"):
        concert.mark_navigation_error(f"geocode: {maps_json.get('status', 'UNKNOWN_ERROR')}")
        return None

    result = maps_json["results"][0]
    location = result["geometry"]["location"]
    concert.lat = location["lat"]
    concert.lng = location["lng"]
    return result["formatted_address"]


async def get_travel_distance(concert: Concert, api_key: str, start_location: str) -> float | None:
    if not concert.address:
        return None

    url = build_url(
        DIRECTIONS_URL,
        {
            "departure_time": "now",
            "destination": concert.address,
            "origin": start_location,
            "key": api_key,
        },
    )
    travel_json = await read_json_async(url)

    if travel_json.get("status") != "OK":
        concert.mark_navigation_error(f"directions: {travel_json.get('status', 'UNKNOWN_ERROR')}")
        return None

    length_meters = travel_json["routes"][0]["legs"][0]["distance"]["value"]
    return round(int(length_meters) / METERS_PER_MILE, 2)


async def enrich_concert(concert: Concert, api_key: str, start_location: str) -> Concert:
    try:
        concert.address = await get_address(concert, api_key)
        if not concert.is_sold_out:
            concert.distance = await get_travel_distance(concert, api_key, start_location)
    except Exception as exc:
        concert.mark_navigation_error(f"lookup failed: {exc}")

    return concert


async def get_tour(artist_url: str, api_key: str, start_location: str) -> dict:
    artist_id = await asyncio.to_thread(get_artist_id, artist_url)
    tour_json = await get_tour_info(artist_id)
    attributes = tour_json.get("data", {}).get("attributes", {})
    concerts = [
        Concert.from_seated_event(artist_id, item["attributes"])
        for item in tour_json.get("included", [])
        if "attributes" in item
    ]

    await asyncio.gather(*(enrich_concert(concert, api_key, start_location) for concert in concerts))
    return {
        "artist_name": attributes.get("name"),
        "image_url": attributes.get("image-url"),
        "concerts": concerts,
    }


async def get_concerts(artist_url: str, api_key: str, start_location: str) -> list[Concert]:
    tour = await get_tour(artist_url, api_key, start_location)
    return tour["concerts"]


async def main() -> None:
    load_dotenv()
    api_key = require_env("GOOGLE_MAPS_API_KEY")
    start_location = require_env("START_LOC")
    artist_url = require_env("ARTIST_URL")

    concerts = await get_concerts(artist_url, api_key, start_location)
    for concert in concerts:
        concert.print_info()


if __name__ == "__main__":
    asyncio.run(main())
