import unittest
from unittest.mock import AsyncMock, patch

import finder
from Concert import Concert


class NavigationFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        finder._fallback_geocode_cache.clear()
        finder._last_fallback_geocoder_request = 0.0
        finder._google_maps_disabled_until = 0.0

    async def test_fallback_geocoder_serializes_into_a_cached_nominatim_result(self) -> None:
        payload = [
            {
                "display_name": "The Anthem, Washington, DC, USA",
                "lat": "38.8808",
                "lon": "-77.0262",
            }
        ]
        with patch.object(
            finder,
            "FALLBACK_GEOCODER_MIN_INTERVAL_SECONDS",
            0,
        ), patch.object(
            finder,
            "read_json_async",
            new=AsyncMock(return_value=payload),
        ) as read_mock:
            first = await finder.fallback_geocode("The Anthem, Washington, DC")
            second = await finder.fallback_geocode("  the anthem,  washington, dc ")

        self.assertEqual(first, second)
        self.assertEqual(first["lat"], 38.8808)
        read_mock.assert_awaited_once()
        requested_url = read_mock.await_args.args[0]
        self.assertIn("nominatim.openstreetmap.org/search?", requested_url)
        self.assertIn("format=jsonv2", requested_url)
        self.assertEqual(
            read_mock.await_args.kwargs["user_agent"],
            finder.FALLBACK_GEOCODER_USER_AGENT,
        )

    async def test_google_quota_geocoding_uses_the_fallback(self) -> None:
        fallback = {
            "address": "Washington, District of Columbia, USA",
            "lat": 38.9072,
            "lng": -77.0369,
        }
        with patch.object(
            finder,
            "read_json_async",
            new=AsyncMock(return_value={"status": "OVER_QUERY_LIMIT", "results": []}),
        ), patch.object(
            finder,
            "fallback_geocode",
            new=AsyncMock(return_value=fallback),
        ) as fallback_mock:
            result = await finder.geocode("Washington, DC", "over-quota-key")

        self.assertEqual(result, fallback)
        fallback_mock.assert_awaited_once_with("Washington, DC")
        self.assertTrue(finder._google_maps_fallback_active())

    async def test_quota_failed_directions_use_an_estimated_distance(self) -> None:
        concert = Concert.from_normalized_event(
            "seated",
            {
                "venue": "Baltimore Soundstage",
                "city": "Baltimore, MD",
                "start_date": "2026-09-20",
            },
        )
        concert.address = "Baltimore Soundstage, Baltimore, MD"
        concert.lat = 39.2894
        concert.lng = -76.6070

        with patch.object(
            finder,
            "read_google_maps_json",
            new=AsyncMock(return_value={"status": "OVER_QUERY_LIMIT"}),
        ), patch.object(
            finder,
            "fallback_geocode",
            new=AsyncMock(
                return_value={
                    "address": "Washington, DC",
                    "lat": 38.9072,
                    "lng": -77.0369,
                }
            ),
        ):
            distance = await finder.get_travel_distance(
                concert,
                "over-quota-key",
                "Washington, DC",
            )

        self.assertIsNotNone(distance)
        self.assertGreater(distance, 40)
        self.assertLess(distance, 50)
        self.assertTrue(concert.distance_is_estimated)
        self.assertTrue(concert.is_drivable)
        self.assertIsNone(concert.navigation_error)

    async def test_confirmed_no_route_stays_unreachable(self) -> None:
        concert = Concert.from_normalized_event(
            "seated",
            {
                "venue": "Island Venue",
                "city": "Across the ocean",
                "start_date": "2026-09-20",
            },
        )
        concert.address = "Island Venue"
        concert.lat = 0.0
        concert.lng = 0.0

        with patch.object(
            finder,
            "read_google_maps_json",
            new=AsyncMock(return_value={"status": "ZERO_RESULTS"}),
        ), patch.object(
            finder,
            "fallback_geocode",
            new=AsyncMock(),
        ) as fallback_mock:
            distance = await finder.get_travel_distance(concert, "maps-key", "Washington, DC")

        self.assertIsNone(distance)
        self.assertFalse(concert.is_drivable)
        self.assertFalse(concert.distance_is_estimated)
        fallback_mock.assert_not_awaited()

    def test_estimate_uses_great_circle_distance_with_a_road_factor(self) -> None:
        distance = finder.estimated_road_distance_miles(
            38.9072,
            -77.0369,
            39.2904,
            -76.6122,
        )

        self.assertGreater(distance, 40)
        self.assertLess(distance, 50)


if __name__ == "__main__":
    unittest.main()
