# Tour page fixtures

Offline HTML snapshots of official artist tour/show pages, used to test backend provider detection and parsing without live HTTP calls.

## Providers collected

| Provider | Count | Notes |
|---|---|---|
| seated | 7 | Works with `get_artist_id()` today |
| bandsintown | 4 | Embedded widget / API JSON in page |
| ticketmaster | 2 | Hardcoded ticket links |
| axs | 1 | Same Billy Strings page, also has AXS links |
| squarespace-events | 2 | Native Squarespace events block |
| songkick | 1 | Songkick Tourbox widget embed; runtime calendar lookup returns full rows |
| eventbrite | 1 | Hardcoded Eventbrite ticket links |
| dice | 2 | Hardcoded dice.fm ticket links |

Songkick, Eventbrite, and Dice were found via targeted web search after the initial artist-site scan missed them.

The saved Songkick HTML intentionally remains an offline widget-id fixture. At runtime, `get_tour()` uses that id (or the id in a direct Songkick artist URL) to read Songkick's public Tourbox calendar and normalize its venue, city, date, and event link fields. If the calendar is unavailable or empty, the UI keeps the existing link-only fallback.

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
