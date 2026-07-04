#!/usr/bin/env python3
"""Download artist tour-page HTML fixtures and write manifest.json."""

from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = ROOT / "fixtures" / "tour-pages"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"
USER_AGENT = "Mozilla/5.0 (compatible; concert-placer/1.0)"
REQUEST_TIMEOUT_SECONDS = 15

FIXTURES: list[dict[str, str]] = [
    {"provider": "seated", "artist_name": "Lawrence", "slug": "lawrence-tour", "url": "https://lawrencetheband.com/tour"},
    {"provider": "seated", "artist_name": "Jack Johnson", "slug": "jack-johnson-home", "url": "https://jackjohnsonmusic.com/"},
    {"provider": "seated", "artist_name": "Bleachers", "slug": "bleachers-tour", "url": "https://www.bleachersmusic.com/tour"},
    {"provider": "seated", "artist_name": "Big Thief", "slug": "big-thief-tour", "url": "https://www.bigthief.net/tour"},
    {"provider": "seated", "artist_name": "Wet Leg", "slug": "wet-leg-tour", "url": "https://www.wetlegband.com/tour"},
    {"provider": "seated", "artist_name": "Japanese Breakfast", "slug": "japanese-breakfast-tour", "url": "https://www.japanesebreakfast.rocks/tour"},
    {"provider": "seated", "artist_name": "Billie Marten", "slug": "billie-marten-tour", "url": "https://www.billiemarten.co.uk/tour"},
    {"provider": "bandsintown", "artist_name": "Jacob Collier", "slug": "jacob-collier-tour", "url": "https://www.jacobcollier.com/tour/"},
    {"provider": "bandsintown", "artist_name": "Glass Animals", "slug": "glass-animals-tour", "url": "https://www.glassanimals.com/tour"},
    {"provider": "bandsintown", "artist_name": "The War on Drugs", "slug": "war-on-drugs-tour", "url": "https://www.thewarondrugs.net/tour"},
    {"provider": "bandsintown", "artist_name": "The Killers", "slug": "killers-tour", "url": "https://www.thekillersmusic.com/tour"},
    {"provider": "ticketmaster", "artist_name": "Billy Strings", "slug": "billy-strings-tour", "url": "https://www.billystrings.com/tour"},
    {"provider": "ticketmaster", "artist_name": "Gregory Alan Isakov", "slug": "gregory-alan-isakov-tour", "url": "https://www.gregoryalanisakov.com/tour"},
    {"provider": "axs", "artist_name": "Billy Strings", "slug": "billy-strings-tour", "url": "https://www.billystrings.com/tour"},
    {"provider": "squarespace-events", "artist_name": "Maggie Rogers", "slug": "maggie-rogers-join", "url": "https://maggierogers.com/join"},
    {"provider": "squarespace-events", "artist_name": "Death Cab for Cutie", "slug": "death-cab-tour", "url": "https://www.deathcabforcutie.com/tour"},
]

SEARCH_NOTES = {
    "songkick": "No official artist tour page with Songkick widget found after scanning 50+ artist sites.",
    "eventbrite": "No official artist tour page with Eventbrite embed found after scanning 50+ artist sites.",
    "dice": "No official artist tour page with Dice widget found after scanning 50+ artist sites.",
}


def fetch_url(url: str) -> tuple[int, bytes]:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()


def detection_signals(html: str, provider: str) -> list[str]:
    signals: list[str] = []
    if match := re.search(r'artist-id="([^"]+)"', html):
        signals.append(f'artist-id="{match.group(1)}"')
    patterns = {
        "seated": [r"seated\.com"],
        "bandsintown": [r"bandsintown\.com", r"rest\.bandsintown\.com"],
        "songkick": [r"widget\.songkick\.com", r"songkick-widget"],
        "eventbrite": [r"eventbrite"],
        "dice": [r"widgets\.dice", r"dice\.fm"],
        "ticketmaster": [r"ticketmaster\.com", r"livenation\.com"],
        "axs": [r"axs\.com"],
        "squarespace-events": [r"squarespace-events-collection", r"squarespace-tourdates"],
    }
    for pattern in patterns.get(provider, []):
        if re.search(pattern, html, re.IGNORECASE):
            signals.append(pattern)
    return signals


def build_entry(spec: dict[str, str], *, fetched_at: str, http_status: int, byte_size: int, signals: list[str]) -> dict:
    rel_path = f"fixtures/tour-pages/{spec['provider']}/{spec['slug']}.html"
    notes = ""
    if spec["provider"] == "seated":
        notes = "Compatible with current get_artist_id() parser."
    if spec["provider"] == "axs" and spec["slug"] == "billy-strings-tour":
        notes = "Same page as ticketmaster fixture; includes axs.com ticket links."
    return {
        "provider": spec["provider"],
        "artist_name": spec["artist_name"],
        "url": spec["url"],
        "filename": rel_path,
        "fetched_at": fetched_at,
        "http_status": http_status,
        "byte_size": byte_size,
        "detection_signals": signals,
        "notes": notes,
    }


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from finder import detect_tour_provider, get_artist_id_from_html

    manifest: list[dict] = []
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    last_host = ""

    for spec in FIXTURES:
        host = spec["url"].split("/")[2]
        if host == last_host:
            time.sleep(1)
        last_host = host

        provider_dir = FIXTURES_DIR / spec["provider"]
        provider_dir.mkdir(parents=True, exist_ok=True)
        output_path = provider_dir / f"{spec['slug']}.html"

        print(f"Fetching {spec['url']} ...", flush=True)
        http_status, body = fetch_url(spec["url"])
        output_path.write_bytes(body)

        html = body.decode("utf-8", errors="replace")
        signals = detection_signals(html, spec["provider"])
        detected = detect_tour_provider(html)
        entry = build_entry(
            spec,
            fetched_at=fetched_at,
            http_status=http_status,
            byte_size=len(body),
            signals=signals,
        )
        if detected and detected != spec["provider"]:
            entry["detected_provider"] = detected
            entry["notes"] = (entry["notes"] + f" Auto-detected as {detected}.").strip()
        if spec["provider"] == "seated":
            try:
                entry["seated_artist_id"] = get_artist_id_from_html(html)
            except RuntimeError:
                entry["notes"] = (entry["notes"] + " Missing artist-id.").strip()
        manifest.append(entry)

    for provider, note in SEARCH_NOTES.items():
        manifest.append(
            {
                "provider": provider,
                "status": "not_found",
                "notes": note,
            }
        )

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len([e for e in manifest if e.get('status') != 'not_found'])} fixtures to {FIXTURES_DIR}")
    print(f"Manifest: {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
