import unittest
from unittest.mock import AsyncMock, patch

import finder
from Concert import Concert


WASHINGTON_DC = {"address": "Washington, DC, USA", "lat": 38.9072, "lng": -77.0369}


def london_concert() -> Concert:
    concert = Concert.from_normalized_event(
        "seated",
        {
            "venue": "O2 Academy Brixton",
            "city": "London, UK",
            "start_date": "2026-09-20",
            "lat": 51.4652,
            "lng": -0.1149,
        },
    )
    return concert


class NavigationFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        finder._fallback_geocode_cache.clear()
        finder._start_geocode_tasks.clear()
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

    async def test_confirmed_no_route_uses_a_straight_line_distance(self) -> None:
        concert = Concert.from_normalized_event(
            "seated",
            {
                "venue": "Island Venue",
                "city": "Honolulu, HI",
                "start_date": "2026-09-20",
            },
        )
        concert.address = "Island Venue, Honolulu, HI"
        concert.lat = 21.3069
        concert.lng = -157.8583

        with patch.object(
            finder,
            "read_google_maps_json",
            new=AsyncMock(return_value={"status": "ZERO_RESULTS"}),
        ), patch.object(
            finder,
            "geocode",
            new=AsyncMock(return_value=WASHINGTON_DC),
        ):
            distance = await finder.get_travel_distance(concert, "maps-key", "Washington, DC")

        self.assertGreater(distance, 4800)
        self.assertLess(distance, 4850)
        self.assertTrue(concert.distance_is_straight_line)
        self.assertFalse(concert.distance_is_estimated)
        self.assertFalse(concert.is_drivable)
        self.assertIsNone(concert.navigation_error)

    async def test_other_continent_skips_the_directions_api(self) -> None:
        concert = london_concert()

        with patch.object(
            finder,
            "read_google_maps_json",
            new=AsyncMock(),
        ) as directions_mock, patch.object(
            finder,
            "geocode",
            new=AsyncMock(return_value=WASHINGTON_DC),
        ) as geocode_mock:
            distance = await finder.enrich_concert(concert, "maps-key", "Washington, DC")
            second = london_concert()
            await finder.enrich_concert(second, "maps-key", "Washington, DC")

        directions_mock.assert_not_awaited()
        geocode_mock.assert_awaited_once()
        self.assertGreater(concert.distance, 3650)
        self.assertLess(concert.distance, 3700)
        self.assertTrue(concert.distance_is_straight_line)
        self.assertIsNone(concert.navigation_error)
        self.assertEqual(second.distance, concert.distance)
        self.assertIs(distance, concert)

    async def test_same_continent_still_asks_for_directions(self) -> None:
        concert = Concert.from_normalized_event(
            "seated",
            {"venue": "Baltimore Soundstage", "city": "Baltimore, MD", "start_date": "2026-09-20"},
        )
        concert.address = "Baltimore Soundstage, Baltimore, MD"
        concert.lat = 39.2894
        concert.lng = -76.6070
        route = {"status": "OK", "routes": [{"legs": [{"distance": {"value": 64000}}]}]}

        with patch.object(
            finder,
            "read_google_maps_json",
            new=AsyncMock(return_value=route),
        ) as directions_mock, patch.object(
            finder,
            "geocode",
            new=AsyncMock(return_value=WASHINGTON_DC),
        ):
            distance = await finder.get_travel_distance(concert, "maps-key", "Washington, DC")

        directions_mock.assert_awaited_once()
        self.assertAlmostEqual(distance, 39.77, places=2)
        self.assertTrue(concert.is_drivable)
        self.assertFalse(concert.distance_is_straight_line)

    def test_road_landmass_spot_checks(self) -> None:
        cases = {
            (40.7128, -74.0060): "north_america",  # New York
            (34.0522, -118.2437): "north_america",  # Los Angeles
            (61.2181, -149.9003): "north_america",  # Anchorage
            (8.9824, -79.5199): "north_america",  # Panama City
            (19.4326, -99.1332): "north_america",  # Mexico City
            (4.7110, -74.0721): "south_america",  # Bogota
            (10.3910, -75.4794): "south_america",  # Cartagena
            (-23.5505, -46.6333): "south_america",  # Sao Paulo
            (-12.0464, -77.0428): "south_america",  # Lima
            (51.5074, -0.1278): "afro_eurasia",  # London
            (38.7223, -9.1393): "afro_eurasia",  # Lisbon
            (35.6762, 139.6503): "afro_eurasia",  # Tokyo
            (30.0444, 31.2357): "afro_eurasia",  # Cairo
            (-33.9249, 18.4241): "afro_eurasia",  # Cape Town
            (-33.8688, 151.2093): "australia",  # Sydney
            (-36.8485, 174.7633): "new_zealand",  # Auckland
            (64.1466, -21.9426): None,  # Reykjavik
        }
        for (lat, lng), expected in cases.items():
            with self.subTest(lat=lat, lng=lng):
                self.assertEqual(finder.road_landmass(lat, lng), expected)

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
