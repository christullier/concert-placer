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

Generate a stable signing secret with `openssl rand -hex 32`. Shared searches
embed the compressed result in the URL and use this server-only secret for an
HMAC signature, so opening a shared link does not repeat artist, provider,
geocoding, or distance API calls. If `SHARE_LINK_SECRET` is omitted, the app
derives a domain-separated signing key from `GOOGLE_MAPS_API_KEY`; setting the
dedicated value is recommended so Maps key rotation does not invalidate links.

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
