import asyncio
from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import json
import os
import re
import time
from typing import Any, Callable
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
SUPPORTED_TOUR_PROVIDERS = {
    "axs",
    "bandsintown",
    "dice",
    "eventbrite",
    "seated",
    "songkick",
    "squarespace-events",
    "ticketmaster",
}
KNOWN_TOUR_PROVIDERS = {
    "axs",
    "bandsintown",
    "dice",
    "eventbrite",
    "songkick",
    "ticketmaster",
}


class ConfigError(RuntimeError):
    pass


class TourPageParseError(RuntimeError):
    pass


class _HeadDataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.scripts: list[dict[str, str]] = []
        self._in_title = False
        self._in_script = False
        self._script_attrs: dict[str, str] = {}
        self._script_data: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {key: value or "" for key, value in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            self.metas.append(attr_dict)
        elif tag == "link":
            self.links.append(attr_dict)
        elif tag == "script":
            self._in_script = True
            self._script_attrs = attr_dict
            self._script_data = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_script:
            self.scripts.append({**self._script_attrs, "text": "".join(self._script_data)})
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._in_script:
            self._script_data.append(data)


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
    for pattern in (
        r'(?:^|\s)artist-id="([^"]+)"',
        r'data-artist-id="([^"]+)"',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    raise RuntimeError("Could not find artist-id in the artist page.")


def get_artist_id(url: str, *, timeout: int = REQUEST_TIMEOUT_SECONDS) -> str:
    html = read_url(url, timeout=timeout)
    if not _has_seated_widget(html):
        raise RuntimeError("Could not find Seated tour data in the artist page.")
    return get_artist_id_from_html(html)


def _has_seated_widget(html: str) -> bool:
    lowered = html.lower()
    has_seated_marker = "seated.com" in lowered or "<seated-events" in lowered
    if has_seated_marker and re.search(r'(?:^|\s)artist-id="[^"]+"', html):
        return True
    if 'data-artist-id="' in html and ("widget.seated.com" in lowered or "cdn.seated.com" in lowered):
        return True
    return False


def detect_tour_provider(html: str) -> str | None:
    lowered = html.lower()
    if "bandsintown" in lowered:
        return "bandsintown"
    if _has_seated_widget(html):
        return "seated"
    if "squarespace-events-collection" in lowered or "squarespace-tourdates" in lowered:
        if not any(
            marker in lowered
            for marker in ("bandsintown", "songkick", "eventbrite", "dice.fm")
        ) and not _has_seated_widget(html):
            return "squarespace-events"
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


def parse_head_data(html: str) -> _HeadDataParser:
    parser = _HeadDataParser()
    parser.feed(html)
    return parser


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"(?i)<br\s*/?>", "\n", value)
    value = re.sub(r"(?i)</(?:p|div|li|td|th|h[1-6]|time|span)>", "\n", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(value).replace("\xa0", " ").split())


def clean_lines(fragment: str) -> list[str]:
    text = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    text = re.sub(r"(?i)</(?:p|div|li|td|th|h[1-6]|time|span|a)>", "\n", text)
    text = re.sub(r"<[^>]+>", "\n", text)
    lines = [" ".join(unescape(line).replace("\xa0", " ").split()) for line in text.splitlines()]
    return [line for line in lines if line]


def meta_content(head: _HeadDataParser, *names: str) -> str | None:
    for name in names:
        for meta in head.metas:
            key = meta.get("property") or meta.get("name") or meta.get("itemprop")
            if key == name and meta.get("content"):
                return meta["content"]
    return None


def parse_artist_metadata(html: str) -> dict[str, str | None]:
    head = parse_head_data(html)
    artist_name = None
    image_url = meta_content(head, "og:image", "twitter:image", "image", "thumbnailUrl")

    for script in head.scripts:
        if "ld+json" not in script.get("type", ""):
            continue
        try:
            data = json.loads(unescape(script.get("text", "").strip()))
        except json.JSONDecodeError:
            continue
        for item in flatten_json(data):
            if not isinstance(item, dict):
                continue
            item_type = json_type(item)
            if item_type in {"WebSite", "MusicGroup", "Organization"} and item.get("name"):
                artist_name = str(item["name"])
                if not image_url and item.get("image"):
                    image_url = string_or_first(item.get("image"))
                break
        if artist_name:
            break

    if not artist_name:
        title = clean_text(meta_content(head, "og:title", "twitter:title") or head.title)
        artist_name = infer_artist_name_from_title(title)

    return {"artist_name": artist_name, "image_url": normalize_image_url(image_url)}


def infer_artist_name_from_title(title: str) -> str | None:
    if not title:
        return None
    parts = re.split(r"\s+[|—-]\s+|\s+-\s+", title)
    for token in reversed(parts):
        candidate = clean_text(token)
        if candidate and not re.search(r"\b(tour|tickets|dates|showtime|official|website|live)\b", candidate, re.I):
            return candidate
    return clean_text(parts[-1] if parts else title) or None


def normalize_image_url(url: str | None) -> str | None:
    if not url:
        return None
    if url.startswith("//"):
        return f"https:{url}"
    return url


def string_or_first(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return string_or_first(value[0])
    return None


def json_type(item: dict[str, Any]) -> str:
    item_type = item.get("@type")
    if isinstance(item_type, list):
        return str(item_type[0]) if item_type else ""
    return str(item_type or "")


def flatten_json(value: Any) -> list[Any]:
    items = [value]
    if isinstance(value, dict):
        for child in value.values():
            items.extend(flatten_json(child))
    elif isinstance(value, list):
        for child in value:
            items.extend(flatten_json(child))
    return items


def normalized_concert(provider: str, venue: str, city: str, start_date: str, **kwargs: Any) -> Concert | None:
    venue = clean_text(venue)
    city = clean_text(city)
    start_date = clean_text(start_date)
    if not (venue or city or start_date):
        return None
    return Concert.from_normalized_event(
        provider,
        {
            "venue": venue,
            "city": city,
            "start_date": start_date,
            "end_date": clean_text(kwargs.get("end_date")),
            "is_sold_out": bool(kwargs.get("is_sold_out")),
        },
    )


def address_to_city(address: Any) -> str:
    if isinstance(address, str):
        return clean_text(address)
    if not isinstance(address, dict):
        return ""
    parts = [
        address.get("streetAddress"),
        address.get("addressLocality"),
        address.get("addressRegion"),
        address.get("postalCode"),
        address.get("addressCountry"),
    ]
    return clean_text(", ".join(str(part) for part in parts if part))


def concert_from_json_ld(provider: str, event: dict[str, Any]) -> Concert | None:
    location = event.get("location") or {}
    if isinstance(location, list):
        location = location[0] if location else {}
    if not isinstance(location, dict):
        location = {}

    venue = clean_text(location.get("name") or event.get("name"))
    city = address_to_city(location.get("address"))
    if not city and venue != event.get("name"):
        city = clean_text(event.get("name"))
    return normalized_concert(
        provider,
        venue,
        city,
        str(event.get("startDate") or ""),
        end_date=str(event.get("endDate") or ""),
    )


def parse_json_ld_concerts(provider: str, html: str) -> list[Concert]:
    head = parse_head_data(html)
    concerts: list[Concert] = []
    seen = set()
    for script in head.scripts:
        if "ld+json" not in script.get("type", ""):
            continue
        try:
            data = json.loads(unescape(script.get("text", "").strip()))
        except json.JSONDecodeError:
            continue
        for item in flatten_json(data):
            if not isinstance(item, dict) or json_type(item) not in {"Event", "MusicEvent"}:
                continue
            concert = concert_from_json_ld(provider, item)
            if not concert:
                continue
            key = (concert.venue, concert.city, concert.start_date)
            if key in seen:
                continue
            seen.add(key)
            concerts.append(concert)
    return concerts


def parse_next_data_concerts(provider: str, html: str) -> list[Concert]:
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not match:
        return []
    try:
        data = json.loads(unescape(match.group(1)))
    except json.JSONDecodeError:
        return []

    concerts = []
    for item in flatten_json(data):
        if not isinstance(item, dict):
            continue
        if {"date", "location", "venue"}.issubset(item.keys()):
            concert = normalized_concert(
                provider,
                str(item.get("venue") or ""),
                str(item.get("location") or ""),
                str(item.get("date") or item.get("writtenDate") or ""),
            )
            if concert:
                concerts.append(concert)
    return dedupe_concerts(concerts)


def parse_month_date(value: str) -> str:
    value = clean_text(value)
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%m/%d/%y", "%m/%d/%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def parse_billy_strings_rows(provider: str, html: str) -> list[Concert]:
    if 'id="date1"' not in html or "Billy Strings" not in html:
        return []
    chunks = re.split(r'(?=<div class="flex flex-col[^"]*.*?\bid="date\d+")', html, flags=re.S)
    concerts: list[Concert] = []
    for chunk in chunks:
        if not re.search(r'\bid="date\d+"', chunk):
            continue
        chunk = re.split(r'(?=<div class="flex flex-col[^"]*.*?\bid="date\d+")', chunk[1:], maxsplit=1, flags=re.S)[0]
        lines = clean_lines(chunk)
        date_index = next((i for i, line in enumerate(lines) if re.match(r"^[A-Z][a-z]{2,8} \d{1,2}, \d{4}$", line)), None)
        if date_index is None or date_index == 0:
            continue
        city = lines[date_index - 1]
        venue = lines[date_index + 1] if len(lines) > date_index + 1 else ""
        concert = normalized_concert(
            provider,
            venue,
            city,
            parse_month_date(lines[date_index]),
            is_sold_out="sold out" in " ".join(lines).lower(),
        )
        if concert:
            concerts.append(concert)
    return dedupe_concerts(concerts)


def parse_gigpress_rows(provider: str, html: str) -> list[Concert]:
    concerts: list[Concert] = []
    for row in re.findall(r'<tr class="gigpress-row[^"]*">(.*?)</tr>', html, re.S):
        date = first_class_text(row, "gigpress-date")
        city = first_class_text(row, "gigpress-city")
        venue = first_class_text(row, "gigpress-venue")
        concert = normalized_concert(provider, venue, city, parse_month_date(date))
        if concert:
            concerts.append(concert)
    return dedupe_concerts(concerts)


def parse_squarespace_eventlist(provider: str, html: str) -> list[Concert]:
    concerts: list[Concert] = []
    for article in re.findall(r'<article class="eventlist-event[^"]*">(.*?)(?=<article class="eventlist-event|\Z)', html, re.S):
        title = first_class_text(article, "eventlist-title")
        date_match = re.search(r'<time class="event-date" datetime="([^"]+)"', article)
        date = date_match.group(1) if date_match else ""
        address = first_map_location(article) or first_class_text(article, "eventlist-meta-address")
        venue = title
        concert = normalized_concert(provider, venue, address, date)
        if concert:
            concerts.append(concert)
    return dedupe_concerts(concerts)


def first_class_text(html: str, class_name: str) -> str:
    match = re.search(
        rf'<[^>]+class="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>(.*?)</[^>]+>',
        html,
        re.S,
    )
    return clean_text(match.group(1)) if match else ""


def first_map_location(html: str) -> str:
    match = re.search(r"https?://maps\.google\.com\?q=([^\"']+)", html)
    return clean_text(match.group(1)) if match else ""


def parse_dice_text(provider: str, html: str) -> list[Concert]:
    concerts: list[Concert] = []
    for paragraph in re.findall(r"<p[^>]*>(.*?)</p>", html, re.S):
        if "dice.fm/event" not in paragraph and not re.search(r"\b\d{2}\.\d{2}\.\d{4}\b", paragraph):
            continue
        text = clean_text(paragraph)
        match = re.match(r"(\d{2}\.\d{2}\.\d{4})\s+-\s+(.*?)\s+-\s+(.*?)(?:\s+-\s+Buy Tickets)?$", text)
        if match:
            start_date, event_name, venue = match.groups()
            venue_parts = [part.strip() for part in venue.rsplit(",", 1)]
            concert = normalized_concert(
                provider,
                venue_parts[0],
                venue_parts[1] if len(venue_parts) > 1 else event_name,
                parse_month_date(start_date),
            )
        else:
            concert = normalized_concert(provider, text, "", "")
        if concert:
            if not (concert.city and concert.start_date):
                concert.mark_navigation_error("snapshot exposes a DICE ticket link but not complete event metadata")
            concerts.append(concert)

    if concerts:
        return dedupe_concerts(concerts)

    heading_match = re.search(r"<h[1-6][^>]*>([^<]*(?:\d{2}\.\d{2}\.\d{2,4})[^<]*)</h[1-6]>", html, re.S)
    if heading_match:
        text = clean_text(heading_match.group(1))
        match = re.match(r"(.+?)\s+(\d{2}\.\d{2}\.\d{2,4})$", text)
        if match:
            venue, date = match.groups()
            concert = normalized_concert(provider, venue, "", parse_month_date(date))
            if concert:
                concert.mark_navigation_error("snapshot exposes a DICE ticket link and date but no city")
                concerts.append(concert)
    return dedupe_concerts(concerts)


def parse_songkick_widget(provider: str, html: str) -> list[Concert]:
    match = re.search(r"songkick\.com/artists/(\d+)", html)
    if not match:
        return []
    concert = normalized_concert(provider, "Songkick Tourbox widget", f"Songkick artist {match.group(1)}", "")
    if concert:
        concert.mark_navigation_error("snapshot contains only a Songkick widget id; live widget extraction is required for event rows")
        return [concert]
    return []


def parse_placeholder(provider: str, html: str, reason: str) -> list[Concert]:
    metadata = parse_artist_metadata(html)
    artist_name = metadata.get("artist_name") or provider
    concert = normalized_concert(provider, f"{artist_name} tour page", provider, "")
    if concert:
        concert.mark_navigation_error(reason)
        return [concert]
    return []


def dedupe_concerts(concerts: list[Concert]) -> list[Concert]:
    deduped = []
    seen = set()
    for concert in concerts:
        key = (concert.venue, concert.city, concert.start_date)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(concert)
    return deduped


def parse_bandsintown_html(html: str) -> list[Concert]:
    concerts = parse_json_ld_concerts("bandsintown", html)
    if concerts:
        return concerts
    concerts = parse_next_data_concerts("bandsintown", html)
    if concerts:
        return concerts
    if "data-artist-name" in html or "bandsintown.com/a/" in html:
        return parse_placeholder(
            "bandsintown",
            html,
            "snapshot contains only a Bandsintown widget/link; live widget extraction is required for event rows",
        )
    return []


def parse_ticketmaster_html(html: str) -> list[Concert]:
    return (
        parse_json_ld_concerts("ticketmaster", html)
        or parse_gigpress_rows("ticketmaster", html)
        or parse_billy_strings_rows("ticketmaster", html)
    )


def parse_axs_html(html: str) -> list[Concert]:
    return parse_billy_strings_rows("axs", html) or parse_json_ld_concerts("axs", html)


def parse_squarespace_events_html(html: str) -> list[Concert]:
    return parse_squarespace_eventlist("squarespace-events", html) or parse_placeholder(
        "squarespace-events",
        html,
        "snapshot contains Squarespace events configuration but no rendered event rows",
    )


def parse_eventbrite_html(html: str) -> list[Concert]:
    return parse_squarespace_eventlist("eventbrite", html) or parse_json_ld_concerts("eventbrite", html)


def parse_dice_html(html: str) -> list[Concert]:
    return parse_dice_text("dice", html) or parse_json_ld_concerts("dice", html)


TOUR_PROVIDER_PARSERS: dict[str, Callable[[str], list[Concert]]] = {
    "axs": parse_axs_html,
    "bandsintown": parse_bandsintown_html,
    "dice": parse_dice_html,
    "eventbrite": parse_eventbrite_html,
    "songkick": lambda html: parse_songkick_widget("songkick", html),
    "squarespace-events": parse_squarespace_events_html,
    "ticketmaster": parse_ticketmaster_html,
}


def parse_tour_page_html(html: str, provider: str | None = None, source_url: str | None = None) -> dict:
    provider = provider or detect_tour_provider(html) or (tour_provider_from_url(source_url) if source_url else None)
    if not provider:
        raise TourPageParseError("Unsupported tour page provider.")
    if provider == "seated":
        concerts = parse_placeholder(
            "seated",
            html,
            "snapshot contains only a Seated artist id; live Seated API lookup is required for event rows",
        )
        metadata = parse_artist_metadata(html)
        return {
            "provider": provider,
            "artist_name": metadata.get("artist_name") or "seated",
            "image_url": metadata.get("image_url"),
            "concerts": concerts,
        }

    parser = TOUR_PROVIDER_PARSERS.get(provider)
    if not parser:
        raise TourPageParseError(f"Unsupported tour page provider: {provider}.")

    concerts = parser(html)
    if not concerts:
        raise TourPageParseError(f"Could not parse concert data from this {provider} tour page.")

    metadata = parse_artist_metadata(html)
    return {
        "provider": provider,
        "artist_name": metadata.get("artist_name") or provider,
        "image_url": metadata.get("image_url"),
        "concerts": concerts,
    }


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
    if relation_type in KNOWN_TOUR_PROVIDERS or tour_provider_from_url(url):
        return 20
    if "bandcamp" in host:
        return 30
    if "soundcloud" in host:
        return 40
    if relation_type == "social network":
        return 50
    return 80


def tour_provider_from_url(url: str) -> str | None:
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    provider_hosts = {
        "axs": ("axs.com",),
        "bandsintown": ("bandsintown.com",),
        "dice": ("dice.fm",),
        "eventbrite": ("eventbrite.com",),
        "songkick": ("songkick.com",),
        "ticketmaster": ("ticketmaster.com", "livenation.com"),
    }
    for provider, hosts in provider_hosts.items():
        if any(host == provider_host or host.endswith(f".{provider_host}") for provider_host in hosts):
            return provider
    return None


def tour_provider_from_candidate(candidate: dict) -> str | None:
    relation_type = (candidate.get("type") or "").lower()
    if relation_type in KNOWN_TOUR_PROVIDERS:
        return relation_type
    return tour_provider_from_url(candidate.get("url") or "")


def probe_tour_provider(url: str, *, timeout: int = ARTIST_PAGE_PROBE_TIMEOUT_SECONDS) -> dict:
    html = read_url(url, timeout=timeout)
    provider = detect_tour_provider(html) or tour_provider_from_url(url)
    result = {"provider": provider}
    if provider == "seated":
        result["seated_artist_id"] = get_artist_id_from_html(html)
    elif provider in TOUR_PROVIDER_PARSERS:
        result["parseable"] = True
    return result


async def resolve_seated_artist_url(mbid: str, max_urls: int = 3) -> dict:
    candidates = await get_musicbrainz_artist_urls(mbid)
    tried_urls = []
    unsupported_provider = None

    for candidate in candidates[:max_urls]:
        url = candidate["url"]
        tried_candidate = dict(candidate)
        tried_urls.append(tried_candidate)
        candidate_provider = tour_provider_from_candidate(candidate)
        if candidate_provider:
            tried_candidate["detected_provider"] = candidate_provider
        if candidate_provider and candidate_provider not in SUPPORTED_TOUR_PROVIDERS:
            if not unsupported_provider:
                unsupported_provider = {"provider": candidate_provider, "url": url}
            continue
        try:
            probe = await asyncio.to_thread(
                probe_tour_provider,
                url,
                timeout=ARTIST_PAGE_PROBE_TIMEOUT_SECONDS,
            )
        except Exception:
            continue
        provider = probe.get("provider")
        if provider:
            tried_candidate["detected_provider"] = provider
        if provider and provider not in SUPPORTED_TOUR_PROVIDERS and not unsupported_provider:
            unsupported_provider = {"provider": provider, "url": url}
            continue
        artist_id = probe.get("seated_artist_id")
        if provider == "seated" and not artist_id:
            continue
        return {
            "artist_url": url,
            "source_url": url,
            "seated_artist_id": artist_id,
            "provider": provider,
            "tried_urls": tried_urls,
            "candidates": candidates[:max_urls],
        }

    return {
        "artist_url": None,
        "source_url": None,
        "seated_artist_id": None,
        "tried_urls": tried_urls,
        "candidates": candidates[:max_urls],
        "unsupported_provider": unsupported_provider,
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
    html = await asyncio.to_thread(read_url, artist_url)
    provider = detect_tour_provider(html) or tour_provider_from_url(artist_url)
    if not provider:
        raise TourPageParseError("Unsupported tour page provider.")

    if provider == "seated":
        artist_id = get_artist_id_from_html(html)
        tour_json = await get_tour_info(artist_id)
        attributes = tour_json.get("data", {}).get("attributes", {})
        concerts = [
            Concert.from_seated_event(artist_id, item["attributes"])
            for item in tour_json.get("included", [])
            if "attributes" in item
        ]
        artist_name = attributes.get("name")
        image_url = attributes.get("image-url")
    else:
        parsed = parse_tour_page_html(html, provider=provider, source_url=artist_url)
        concerts = parsed["concerts"]
        artist_name = parsed["artist_name"]
        image_url = parsed["image_url"]

    await asyncio.gather(*(enrich_concert(concert, api_key, start_location) for concert in concerts))
    return {
        "artist_name": artist_name,
        "image_url": image_url,
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
