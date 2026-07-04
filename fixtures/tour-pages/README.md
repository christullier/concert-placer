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

**Not found** (documented in `manifest.json`): Songkick, Eventbrite, Dice — none appeared in 50+ artist site scans.

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
  songkick/      (empty — not found yet)
  eventbrite/    (empty — not found yet)
  dice/          (empty — not found yet)
```
