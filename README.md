# concert-placer

Find upcoming concerts for an artist and print driving distance from a configured start location.

## Usage

Create a `.env` file with:

```text
GOOGLE_MAPS_API_KEY=...
START_LOC=...
ARTIST_URL=...
```

Run the CLI:

```bash
env/bin/python finder.py
```

## Web UI

A mobile-friendly web UI (map + concert cards) is available via FastAPI:

```bash
env/bin/pip install -r requirements.txt
env/bin/uvicorn app:app --reload
```

Then open http://localhost:8000. Enter an artist page URL and a start location;
venues are plotted on a map with tappable cards showing driving distance. The
form prefills `ARTIST_URL` / `START_LOC` from `.env` if set.

## TODO
- [x] scrape data from artist website
- [x] Concert class
- [x] fetch venue distances concurrently
- [ ] Artist class? nah


## PROGRESS 
- concert data
  - scrape info from artists' website (currently only works with the Seated API)
  - objectify data
- plot an address on google maps or something
  - tried Open Street Map (OSM) without luck, their naming 
- find a way to find the distance from a start location
