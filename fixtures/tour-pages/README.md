# Tour page fixtures

Offline HTML snapshots of official artist tour/show pages, used to test backend provider detection and parsing without live HTTP calls.

## Providers collected

| Provider | Count | Notes |
|---|---|---|
| seated | 7 | Works with `get_artist_id()` today |
| bandsintown | 4 | Offline widget fixtures; runtime widget API returns full rows |
| ticketmaster | 2 | Hardcoded ticket links |
| axs | 1 | Same Billy Strings page, also has AXS links |
| squarespace-events | 2 | Native Squarespace events block |
| songkick | 1 | Songkick Tourbox widget embed; runtime calendar lookup returns full rows |
| eventbrite | 1 | Hardcoded Eventbrite ticket links |
| dice | 2 | Hardcoded dice.fm ticket links |

Songkick, Eventbrite, and Dice were found via targeted web search after the initial artist-site scan missed them.

The saved Songkick HTML intentionally remains an offline widget-id fixture. At runtime, `get_tour()` uses that id (or the id in a direct Songkick artist URL) to read Songkick's public Tourbox calendar and normalize its venue, city, date, and event link fields. If the calendar is unavailable or empty, the UI keeps the existing link-only fallback.

Direct Bandsintown artist URLs follow the same runtime pattern: `get_tour()` reads the public V4 widget events endpoint using the artist id instead of fetching the Cloudflare-protected artist page. If Songkick has no current rows, its calendar artist name is also checked against Bandsintown so older saved Songkick links can still surface live dates.

## Refresh fixtures

```bash
env/bin/python scripts/fetch_tour_fixtures.py
```

## Run tests

```bash
env/bin/python -m unittest test_tour_fixtures.py
```

## Layout

```
fixtures/tour-pages/
  manifest.json
  seated/
  bandsintown/
  ticketmaster/
  axs/
  squarespace-events/
  songkick/
  eventbrite/
  dice/
```
