import asyncio
import json
import os
import re
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from dotenv import load_dotenv

from Concert import Concert

GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"
DIRECTIONS_URL = "https://maps.googleapis.com/maps/api/directions/json"
SEATED_TOUR_URL = "https://cdn.seated.com/api/tour/{artist_id}"
METERS_PER_MILE = 1609.344
REQUEST_TIMEOUT_SECONDS = 30
# Seated's CDN and some artist sites reject the default urllib User-Agent.
USER_AGENT = "Mozilla/5.0 (compatible; concert-placer/1.0)"


class ConfigError(RuntimeError):
    pass


def require_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def build_url(base_url: str, params: dict[str, str]) -> str:
    return f"{base_url}?{urlencode(params)}"


def read_url(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as page:
        return page.read().decode("utf-8")


def read_json(url: str) -> dict:
    return json.loads(read_url(url))


async def read_json_async(url: str) -> dict:
    return await asyncio.to_thread(read_json, url)


def get_artist_id(url: str) -> str:
    html = read_url(url)

    # find 'artist-id'
    match = re.search(r'artist-id="([^"]+)"', html)
    if not match:
        raise RuntimeError("Could not find artist-id in the artist page.")

    return match.group(1)


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
    if concert.is_sold_out:
        return concert

    try:
        concert.address = await get_address(concert, api_key)
        concert.distance = await get_travel_distance(concert, api_key, start_location)
    except Exception as exc:
        concert.mark_navigation_error(f"lookup failed: {exc}")

    return concert


async def get_concerts(artist_url: str, api_key: str, start_location: str) -> list[Concert]:
    artist_id = await asyncio.to_thread(get_artist_id, artist_url)
    tour_json = await get_tour_info(artist_id)
    concerts = [
        Concert.from_seated_event(artist_id, item["attributes"])
        for item in tour_json.get("included", [])
        if "attributes" in item
    ]

    await asyncio.gather(*(enrich_concert(concert, api_key, start_location) for concert in concerts))
    return concerts


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
