# concert-placer

Find upcoming concerts for an artist and print driving distance from a configured start location.

## Usage

Create a `.env` file with:

```text
GOOGLE_MAPS_API_KEY=...
SHARE_LINK_SECRET=...
START_LOC=...
ARTIST_URL=...
```

`GOOGLE_MAPS_API_KEY` is optional. When it is missing, denied, or over quota,
the app geocodes through the OpenStreetMap Nominatim fallback and displays an
explicitly marked approximate road distance. The estimate is great-circle
distance multiplied by 1.25; it is useful for plotting, sorting, and filtering,
but it is not a verified driving route.

The public Nominatim fallback is serialized to one request per second and
cached in memory. Override `FALLBACK_GEOCODER_URL` to use another or self-hosted
Nominatim instance, and set `FALLBACK_GEOCODER_USER_AGENT` if you need a custom
identifying User-Agent.

Generate a stable signing secret with `openssl rand -hex 32`. Shared searches
embed the compressed result in the URL and use this server-only secret for an
HMAC signature, so opening a shared link does not repeat artist, provider,
geocoding, or distance API calls. If `SHARE_LINK_SECRET` is omitted, the app
derives a domain-separated signing key from `GOOGLE_MAPS_API_KEY`; set the
dedicated secret when running without Google or when Maps key rotation should
not invalidate links.

Run:

```bash
env/bin/python finder.py
```

Run the web app:

```bash
env/bin/python app.py
```

By default the web server binds to `0.0.0.0:8000`. Override with `HOST` and
`PORT` if needed:

```bash
HOST=0.0.0.0 PORT=8080 env/bin/python app.py
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
