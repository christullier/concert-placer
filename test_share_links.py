import hashlib
import json
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import app as web_app
from Concert import Concert


SIGNING_KEY = hashlib.sha256(b"concert-placer-test-key").digest()


def sample_payload() -> dict:
    return {
        "v": 1,
        "created_at": "2026-07-13T12:00:00+00:00",
        "artist_url": "https://example.com/artists/japanese-breakfast",
        "start_location": "Washington, DC",
        "result": {
            "artist": {
                "name": "Japanese Breakfast",
                "image_url": "https://example.com/artist.jpg",
            },
            "start": {
                "address": "Washington, DC, USA",
                "lat": 38.9072,
                "lng": -77.0369,
            },
            "concerts": [
                {
                    "artist_id": "seated",
                    "venue": "The Anthem",
                    "city": "Washington, DC",
                    "start_date": "2026-09-20",
                    "end_date": None,
                    "is_sold_out": False,
                    "address": "901 Wharf St SW",
                    "lat": 38.8808,
                    "lng": -77.0262,
                    "distance": 3.2,
                    "is_drivable": True,
                    "navigation_error": None,
                    "ticket_url": "https://example.com/tickets",
                }
            ],
            "parse_status": "full",
            "external_url": None,
            "provider": "seated",
        },
    }


class ShareTokenTests(unittest.TestCase):
    def test_round_trip_preserves_the_complete_result(self):
        payload = sample_payload()

        token = web_app._encode_share_token(payload, SIGNING_KEY)

        self.assertEqual(web_app._decode_share_token(token, SIGNING_KEY), payload)
        self.assertLessEqual(len(token), web_app.MAX_SHARE_TOKEN_CHARS)

    def test_payload_tampering_invalidates_the_signature(self):
        token = web_app._encode_share_token(sample_payload(), SIGNING_KEY)
        encoded_payload, signature = token.split(".")
        replacement = "A" if encoded_payload[0] != "A" else "B"
        modified = replacement + encoded_payload[1:] + "." + signature

        with self.assertRaisesRegex(web_app.ShareLinkError, "modified or is invalid"):
            web_app._decode_share_token(modified, SIGNING_KEY)

    def test_signature_tampering_is_rejected(self):
        token = web_app._encode_share_token(sample_payload(), SIGNING_KEY)
        encoded_payload, signature = token.split(".")
        replacement = "A" if signature[-1] != "A" else "B"
        modified = encoded_payload + "." + signature[:-1] + replacement

        with self.assertRaisesRegex(web_app.ShareLinkError, "modified or is invalid"):
            web_app._decode_share_token(modified, SIGNING_KEY)

    def test_embedded_json_cannot_close_its_script_tag(self):
        payload = sample_payload()
        payload["result"]["artist"]["name"] = "</script><script>alert(1)</script>"

        html = web_app._shared_index_html(payload)

        self.assertNotIn("</script><script>alert(1)</script>", html)
        self.assertIn("\\u003c/script>", html)
        self.assertLess(
            html.index('id="shared-search-data"'),
            html.index('src="/static/app.js'),
        )

    def test_signing_key_prefers_dedicated_secret(self):
        with patch.dict(
            "os.environ",
            {
                "SHARE_LINK_SECRET": "dedicated-share-secret",
                "GOOGLE_MAPS_API_KEY": "maps-key",
            },
        ):
            dedicated_key = web_app._share_signing_key()
        with patch.dict(
            "os.environ",
            {"GOOGLE_MAPS_API_KEY": "maps-key"},
            clear=True,
        ):
            fallback_key = web_app._share_signing_key()

        self.assertNotEqual(dedicated_key, fallback_key)


class SharedPageTests(unittest.IsolatedAsyncioTestCase):
    async def test_concert_lookup_returns_a_signed_self_contained_path(self):
        start = {"address": "Washington, DC, USA", "lat": 38.9072, "lng": -77.0369}
        concert = Concert.from_normalized_event(
            "seated",
            {
                "venue": "The Anthem",
                "city": "Washington, DC",
                "start_date": "2026-09-20",
                "ticket_url": "https://example.com/tickets",
            },
        )
        tour = {
            "artist_name": "Japanese Breakfast",
            "image_url": "https://example.com/artist.jpg",
            "concerts": [concert],
            "parse_status": "full",
            "external_url": None,
            "provider": "seated",
        }
        request = web_app.ConcertRequest(
            artist_url="https://example.com/artists/japanese-breakfast",
            start_location="Washington, DC",
        )

        with (
            patch.dict("os.environ", {"GOOGLE_MAPS_API_KEY": "maps-key"}, clear=True),
            patch.object(web_app, "geocode_start", new=AsyncMock(return_value=start)),
            patch.object(web_app, "get_tour", new=AsyncMock(return_value=tour)),
            patch.object(web_app, "_upsert_artist", new=AsyncMock()),
        ):
            result = await web_app.lookup_concerts(request)
            token = result["share_path"].removeprefix("/share/")
            payload = web_app._decode_share_token(token, web_app._share_signing_key())

        self.assertEqual(payload["artist_url"], request.artist_url)
        self.assertEqual(payload["start_location"], request.start_location)
        self.assertEqual(payload["result"]["concerts"][0]["venue"], "The Anthem")
        self.assertNotIn("share_path", payload["result"])

    async def test_concert_lookup_works_without_a_google_maps_key(self):
        start = {"address": "Washington, DC, USA", "lat": 38.9072, "lng": -77.0369}
        tour = {
            "artist_name": "Japanese Breakfast",
            "image_url": None,
            "concerts": [],
            "parse_status": "no_shows",
            "external_url": "https://example.com/tour",
            "provider": "seated",
        }
        request = web_app.ConcertRequest(
            artist_url="https://example.com/tour",
            start_location="Washington, DC",
        )

        with (
            patch.dict("os.environ", {"SHARE_LINK_SECRET": "test-secret"}, clear=True),
            patch.object(web_app, "geocode_start", new=AsyncMock(return_value=start)),
            patch.object(web_app, "get_tour", new=AsyncMock(return_value=tour)) as tour_mock,
            patch.object(web_app, "_upsert_artist", new=AsyncMock()),
        ):
            result = await web_app.lookup_concerts(request)

        self.assertIsNotNone(result["share_path"])
        self.assertEqual(tour_mock.await_args.args[1], "")

    async def test_shared_page_contains_verified_data(self):
        payload = sample_payload()
        token = web_app._encode_share_token(payload, SIGNING_KEY)

        with patch.object(web_app, "_share_signing_key", return_value=SIGNING_KEY):
            response = await web_app.shared_search(token)

        body = response.body.decode("utf-8")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertIn('id="shared-search-data"', body)
        self.assertIn(json.dumps(payload["start_location"]), body)

    async def test_modified_shared_page_path_returns_not_found(self):
        token = web_app._encode_share_token(sample_payload(), SIGNING_KEY)
        encoded_payload, signature = token.split(".")
        modified = encoded_payload + "." + signature[:-1] + "A"

        with patch.object(web_app, "_share_signing_key", return_value=SIGNING_KEY):
            with self.assertRaises(HTTPException) as raised:
                await web_app.shared_search(modified)

        self.assertEqual(raised.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
