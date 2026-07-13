# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run the web app (FastAPI + Uvicorn, binds 0.0.0.0:8000 by default)
env/bin/python app.py
# Override host/port:
HOST=0.0.0.0 PORT=8080 env/bin/python app.py

# Run the CLI lookup (reads .env for GOOGLE_MAPS_API_KEY, START_LOC, ARTIST_URL)
env/bin/python finder.py

# Run the full test suite (offline, uses saved HTML fixtures — no network calls)
env/bin/python -m unittest test_tour_fixtures.py

# Run a single test
env/bin/python -m unittest test_tour_fixtures.TourFixtureTests.test_each_fixture_parses_to_normalized_concerts

# Refresh tour-page HTML fixtures (hits live artist sites over the network)
env/bin/python scripts/fetch_tour_fixtures.py
```

There is no separate lint/build step; `env/` is the project's local virtualenv (Python 3.12), already provisioned — use `env/bin/python` rather than a bare `python`.

Required `.env` values: `GOOGLE_MAPS_API_KEY`, `START_LOC` (used by `finder.py`'s CLI path and as the default prefill for `/api/config`), and `ARTIST_URL` (CLI-only). The web app never leaks the Maps API key to the client — see `/api/config` in `app.py`.

## Architecture

**Core idea:** given an artist's tour/homepage URL, detect which third-party ticketing/tour-listing platform it uses, scrape a static HTML snapshot of that page into a normalized list of `Concert` objects, then geocode each venue and compute driving distance/time from a configured start location via the Google Maps Directions API.

### Provider detection & parsing pipeline (`finder.py`)

Tour pages are scraped as static HTML (`read_url`/`read_url` via `urllib`, not a headless browser), so parsing only works on providers that render event data server-side or embed it in the initial HTML (JSON-LD, `__NEXT_DATA__`, inline widget markup, etc.).

1. `detect_tour_provider(html)` sniffs the raw HTML for provider fingerprints (Seated widget markers, Bandsintown, Squarespace native events, Songkick, Eventbrite, DICE, Ticketmaster/LiveNation, AXS). Falls back to `tour_provider_from_url(url)` (hostname match) when sniffing is inconclusive.
2. `SUPPORTED_TOUR_PROVIDERS` vs `KNOWN_TOUR_PROVIDERS`: the former is the set with a working parser wired up in `TOUR_PROVIDER_PARSERS`; the latter is providers we can *recognize* even without a parser (used to rank/skip candidate URLs and to surface "unsupported provider" errors distinctly from "couldn't parse").
3. Each provider has a dedicated parse function (`parse_bandsintown_html`, `parse_ticketmaster_html`, `parse_squarespace_events_html`, `parse_dice_html`, etc.) registered in `TOUR_PROVIDER_PARSERS`. Most try several extraction strategies in priority order (e.g. JSON-LD → `__NEXT_DATA__` → hand-rolled row scraping) and fall through to a placeholder `Concert` with `mark_navigation_error(...)` set when the page only proves the provider is present but exposes no real event rows (e.g. a client-rendered widget with no server-rendered rows). Seated is special-cased in `parse_tour_page_html`/`get_tour`: it doesn't scrape event rows from HTML at all — it extracts an `artist-id` and calls the live Seated CDN API (`get_tour_info`) for event data.
4. All parsers converge on `Concert.from_normalized_event`/`normalized_concert(...)`, deduped via `dedupe_concerts` on `(venue, city, start_date)`.
5. `enrich_concert` then geocodes each concert's venue+city (`get_address`) and computes drive distance from `start_location` (`get_travel_distance`), running concurrently over all concerts via `asyncio.gather`. Sold-out shows skip the distance lookup. Any failure (geocode error, no route, exception) is recorded via `Concert.mark_navigation_error` rather than raised — the concert list always renders, with per-row error state instead of a failed request.

### Artist resolution flow (MusicBrainz → tour page)

When a user only knows an artist name, not a URL: `search_musicbrainz_artists` (MusicBrainz search API) → user picks an MBID → `resolve_seated_artist_url(mbid)` calls `get_musicbrainz_artist_urls` (MusicBrainz artist relations) and ranks candidate URLs via `artist_url_priority` (official homepage first, then known tour-provider domains, then Bandcamp/SoundCloud/social, with a blocklist of unhelpful hosts like Wikipedia/Spotify/last.fm in `is_useful_artist_url`). `insert_official_tour_attempt` inserts a synthetic `<homepage>/tour` guess right after an official homepage candidate. Candidates are probed live (`probe_tour_provider`) in ranked order; a provider match with real static event rows wins immediately, one with only a bare provider marker (no rows) is kept as `eventless_fallback` in case nothing better is found, and unsupported providers are recorded but skipped. All of this — including every tried URL and why — is returned to the caller so the frontend can explain resolution failures instead of just erroring out.

MusicBrainz requests are rate-limited to 1/sec via a module-level lock (`_musicbrainz_lock`/`_last_musicbrainz_request`) per MusicBrainz's API terms.

### Web app (`app.py` + `static/`)

FastAPI backend, vanilla JS/HTML/CSS frontend (no build step, no framework) served as static files.

- `POST /api/concerts` — the main lookup: geocodes the start location and fetches/parses the tour concurrently (`asyncio.gather`), then persists the artist to `saved_artists.json` (keyed by a `sha1(url)[:10]` id via `_artist_id`).
- `GET /api/artist-search`, `POST /api/resolve-artist` — the MusicBrainz-driven resolution flow described above, used when the frontend doesn't have a direct tour URL yet.
- `GET /api/artists`, `POST /api/artists`, `DELETE /api/artists/{id}` — simple JSON-file-backed saved-artist list (`saved_artists.json`), guarded by an `asyncio.Lock` since FastAPI may interleave concurrent requests.
- `GET /api/location-default` — best-effort IP geolocation for prefilling the start-location field; tries providers from `IP_GEOLOCATION_URLS` (comma-separated, overridable via env) in order and returns the first success. `_public_client_ip` only trusts globally-routable IPs (checks `X-Forwarded-For` then `request.client.host`), since this is used to pick a plausible default, not for anything security-sensitive.
- `static/app.js` owns all frontend state: search/resolve flow, Google Maps rendering (`initMap`, markers, popovers), list/map view toggle for mobile, sort and filter controls (distance, date range, sold-out), and the saved-artists chip list. There's no bundler — it's a single script loaded directly by `index.html`.

### Fixture-based test suite

`test_tour_fixtures.py` never makes network calls. It validates parsing against offline HTML snapshots in `fixtures/tour-pages/<provider>/*.html`, indexed by `fixtures/tour-pages/manifest.json` (one entry per fixture: provider, artist name, filename, detection signals, and — for Seated — the expected `seated_artist_id`). When adding support for a new provider or fixing a parser bug, add/update a fixture rather than relying on live HTTP in tests. Regenerate fixtures with `scripts/fetch_tour_fixtures.py`, which re-fetches each URL in its own `FIXTURES` list and rewrites the manifest — that script's `FIXTURES` list is the source of truth for which artist pages are tracked, so add new ones there first.

Some fixtures are intentionally "partial" (e.g. a provider widget present but no server-rendered rows) — these are tracked in the `partial_expected` set in `test_each_fixture_parses_to_normalized_concerts` and are asserted to produce a `navigation_error` rather than full concert data.
