# concert-placer

Find upcoming concerts for an artist and print driving distance from a configured start location.

## Usage

Create a `.env` file with:

```text
GOOGLE_MAPS_API_KEY=...
START_LOC=...
ARTIST_URL=...
```

Run:

```bash
env/bin/python finder.py
```

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
