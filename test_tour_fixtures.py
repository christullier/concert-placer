import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from Concert import Concert
from finder import (
    bandsintown_artist_id_from_url,
    build_tour_result,
    detect_tour_provider,
    get_artist_id_from_html,
    get_tour,
    insert_official_tour_attempt,
    parse_bandsintown_events,
    parse_tour_page_html,
    parse_songkick_calendar,
    probe_tour_provider,
    ranked_artist_urls,
    songkick_artist_id_from_url,
)

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures" / "tour-pages"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


def load_manifest() -> list[dict]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def saved_fixtures(manifest: list[dict]) -> list[dict]:
    return [entry for entry in manifest if entry.get("status") != "not_found"]


def sample_songkick_payload() -> dict:
    return {
        "resultsPage": {
            "status": "ok",
            "results": {
                "performance": [
                    {
                        "artist": {"id": 393441, "displayName": "DJ Krush"},
                        "event": {
                            "displayName": "DJ Krush at Stereo",
                            "status": "ok",
                            "uri": "https://www.songkick.com/concerts/43124271-dj-krush-at-stereo",
                            "start": {"date": "2026-10-02"},
                            "venue": {"displayName": "Stereo"},
                            "location": {"city": "Glasgow, UK"},
                        },
                        "directTicketLink": "https://tickets.example/songkick",
                    }
                ]
            },
        }
    }


def empty_songkick_payload() -> dict:
    return {
        "resultsPage": {
            "status": "ok",
            "results": {},
            "totalEntries": 0,
            "artist": {"id": 7286084, "name": "Anderson .Paak"},
        }
    }


def sample_bandsintown_payload() -> list[dict]:
    return [
        {
            "id": "108605695",
            "url": "https://www.bandsintown.com/e/108605695",
            "datetime": "2026-08-15T19:00:00",
            "title": "KCON LA 2026",
            "artist": {
                "id": "8679931",
                "name": "Anderson .Paak",
                "image_url": "https://photos.bandsintown.com/large/18112721.jpeg",
            },
            "venue": {
                "name": "Crypto.com Arena",
                "location": "Los Angeles, CA",
            },
            "lineup": ["Anderson .Paak"],
            "offers": [],
            "artist_id": "8679931",
            "starts_at": "2026-08-15T19:00:00",
            "ends_at": "",
            "sold_out": False,
        },
        {
            "id": "1039405923",
            "url": "https://www.bandsintown.com/e/1039405923",
            "venue": {"name": "E11EVEN", "location": "Miami, FL"},
            "lineup": ["Anderson .Paak"],
            "offers": [
                {
                    "status": "available",
                    "url": "https://www.bandsintown.com/t/1039405923",
                }
            ],
            "artist_id": "8679931",
            "starts_at": "2026-09-19T20:00:00",
            "ends_at": "",
            "sold_out": False,
        },
    ]


class TourFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = load_manifest()
        cls.fixtures = saved_fixtures(cls.manifest)

    def test_manifest_exists_with_multiple_providers(self) -> None:
        providers = {entry["provider"] for entry in self.fixtures}
        self.assertGreaterEqual(len(self.fixtures), 11)
        self.assertGreaterEqual(len(providers), 8)
        for provider in ("seated", "bandsintown", "songkick", "eventbrite", "dice"):
            self.assertIn(provider, providers)

    def test_each_fixture_file_is_present_and_nonempty(self) -> None:
        for entry in self.fixtures:
            path = ROOT / entry["filename"]
            with self.subTest(provider=entry["provider"], artist=entry["artist_name"]):
                self.assertTrue(path.is_file(), msg=str(path))
                self.assertGreaterEqual(path.stat().st_size, 5_000)

    def test_seated_fixtures_parse_artist_id(self) -> None:
        seated = [entry for entry in self.fixtures if entry["provider"] == "seated"]
        self.assertGreaterEqual(len(seated), 3)
        for entry in seated:
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            with self.subTest(artist=entry["artist_name"]):
                artist_id = get_artist_id_from_html(html)
                self.assertTrue(artist_id)
                self.assertEqual(entry.get("seated_artist_id"), artist_id)

    def test_provider_detection_matches_manifest(self) -> None:
        for entry in self.fixtures:
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            detected = detect_tour_provider(html)
            with self.subTest(provider=entry["provider"], artist=entry["artist_name"]):
                if entry["provider"] in {"ticketmaster", "axs"}:
                    self.assertIn(
                        detected,
                        {"ticketmaster", "axs"},
                        msg="Pages with mixed ticket vendors should detect one of them.",
                    )
                else:
                    self.assertEqual(detected, entry["provider"])

    def test_each_fixture_parses_to_normalized_concerts(self) -> None:
        partial_expected = {
            ("bandsintown", "The War on Drugs"),
            ("bandsintown", "The Killers"),
            ("dice", "Hannah Grae"),
            ("seated", "Big Thief"),
            ("seated", "Billie Marten"),
            ("seated", "Bleachers"),
            ("seated", "Jack Johnson"),
            ("seated", "Japanese Breakfast"),
            ("seated", "Lawrence"),
            ("seated", "Wet Leg"),
            ("songkick", "Job Alone & Friends"),
            ("squarespace-events", "Death Cab for Cutie"),
            ("squarespace-events", "Maggie Rogers"),
        }

        for entry in self.fixtures:
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            parsed = parse_tour_page_html(html, provider=entry.get("detected_provider") or entry["provider"])
            concerts = parsed["concerts"]
            expected_partial = (entry["provider"], entry["artist_name"]) in partial_expected

            with self.subTest(provider=entry["provider"], artist=entry["artist_name"]):
                self.assertEqual(parsed["provider"], entry.get("detected_provider") or entry["provider"])
                self.assertTrue(parsed["artist_name"])
                self.assertGreaterEqual(len(concerts), 1)
                for concert in concerts:
                    self.assertTrue(concert.venue or concert.city)

                if expected_partial:
                    self.assertTrue(
                        any(concert.navigation_error for concert in concerts),
                        msg="Partial fixtures should explain why full event rows are unavailable.",
                    )
                else:
                    self.assertTrue(
                        any(concert.venue and concert.city and concert.start_date for concert in concerts),
                        msg="Full fixtures should include venue, city, and date.",
                    )

    def test_bare_artist_id_is_not_enough_to_detect_seated(self) -> None:
        html = '<div class="tour-widget" artist-id="abc123"></div>'
        self.assertIsNone(detect_tour_provider(html))

    def test_songkick_artist_id_from_direct_url(self) -> None:
        self.assertEqual(
            songkick_artist_id_from_url("https://www.songkick.com/artists/393441-dj-krush/calendar"),
            "393441",
        )

    def test_bandsintown_artist_id_from_direct_url(self) -> None:
        self.assertEqual(
            bandsintown_artist_id_from_url("https://www.bandsintown.com/a/8679931-anderson-paak"),
            "8679931",
        )

    def test_bandsintown_events_parse_full_event_rows(self) -> None:
        parsed = parse_bandsintown_events(sample_bandsintown_payload())

        self.assertEqual(parsed["artist_name"], "Anderson .Paak")
        self.assertIn("18112721", parsed["image_url"])
        self.assertEqual(len(parsed["concerts"]), 2)
        first, second = parsed["concerts"]
        self.assertEqual(
            (first.venue, first.city, first.start_date),
            ("Crypto.com Arena", "Los Angeles, CA", "2026-08-15"),
        )
        self.assertEqual(first.ticket_url, "https://www.bandsintown.com/e/108605695")
        self.assertEqual(second.ticket_url, "https://www.bandsintown.com/t/1039405923")

    def test_direct_bandsintown_probe_uses_events_api_without_fetching_artist_html(self) -> None:
        with patch("finder.read_json", return_value=sample_bandsintown_payload()) as read_json_mock, patch(
            "finder.read_url"
        ) as read_url_mock:
            result = probe_tour_provider("https://www.bandsintown.com/a/8679931-anderson-paak")

        self.assertEqual(result["provider"], "bandsintown")
        self.assertEqual(result["bandsintown_artist_id"], "8679931")
        self.assertTrue(result["has_events"])
        read_json_mock.assert_called_once()
        read_url_mock.assert_not_called()

    def test_direct_bandsintown_url_runs_through_full_tour_lookup(self) -> None:
        with patch(
            "finder.get_bandsintown_tour_info",
            new=AsyncMock(return_value=sample_bandsintown_payload()),
        ), patch(
            "finder.enrich_concert",
            new=AsyncMock(side_effect=lambda concert, *_args: concert),
        ), patch("finder.read_url") as read_url_mock:
            result = asyncio.run(
                get_tour(
                    "https://www.bandsintown.com/a/8679931-anderson-paak",
                    "unused-maps-key",
                    "Washington, DC",
                )
            )

        self.assertEqual(result["provider"], "bandsintown")
        self.assertEqual(result["parse_status"], "full")
        self.assertEqual(result["artist_name"], "Anderson .Paak")
        self.assertEqual(len(result["concerts"]), 2)
        read_url_mock.assert_not_called()

    def test_songkick_calendar_parses_full_event_rows(self) -> None:
        parsed = parse_songkick_calendar(sample_songkick_payload())

        self.assertEqual(parsed["artist_name"], "DJ Krush")
        self.assertIn("/393441/", parsed["image_url"])
        self.assertEqual(len(parsed["concerts"]), 1)
        concert = parsed["concerts"][0]
        self.assertEqual(
            (concert.venue, concert.city, concert.start_date),
            ("Stereo", "Glasgow, UK", "2026-10-02"),
        )
        self.assertEqual(concert.ticket_url, "https://tickets.example/songkick")
        self.assertIsNone(concert.navigation_error)

    def test_empty_songkick_calendar_keeps_artist_metadata(self) -> None:
        parsed = parse_songkick_calendar(empty_songkick_payload())

        self.assertEqual(parsed["artist_name"], "Anderson .Paak")
        self.assertIn("/7286084/", parsed["image_url"])
        self.assertEqual(parsed["concerts"], [])

    def test_direct_songkick_probe_uses_calendar_without_fetching_artist_html(self) -> None:
        with patch("finder.read_json", return_value=sample_songkick_payload()) as read_json_mock, patch(
            "finder.read_url"
        ) as read_url_mock:
            result = probe_tour_provider("https://www.songkick.com/artists/393441-dj-krush/calendar")

        self.assertEqual(result["provider"], "songkick")
        self.assertEqual(result["songkick_artist_id"], "393441")
        self.assertTrue(result["has_events"])
        read_json_mock.assert_called_once()
        read_url_mock.assert_not_called()

    def test_direct_songkick_url_runs_through_full_tour_lookup(self) -> None:
        with patch(
            "finder.get_songkick_tour_info",
            new=AsyncMock(return_value=sample_songkick_payload()),
        ), patch(
            "finder.enrich_concert",
            new=AsyncMock(side_effect=lambda concert, *_args: concert),
        ), patch("finder.read_url") as read_url_mock:
            result = asyncio.run(
                get_tour(
                    "https://www.songkick.com/artists/393441-dj-krush/calendar",
                    "unused-maps-key",
                    "Washington, DC",
                )
            )

        self.assertEqual(result["provider"], "songkick")
        self.assertEqual(result["parse_status"], "full")
        self.assertEqual(result["artist_name"], "DJ Krush")
        self.assertEqual(len(result["concerts"]), 1)
        read_url_mock.assert_not_called()

    def test_songkick_calendar_failure_falls_back_to_external_link(self) -> None:
        with patch(
            "finder.get_songkick_tour_info",
            new=AsyncMock(side_effect=RuntimeError("calendar unavailable")),
        ), patch("finder.read_url") as read_url_mock:
            result = asyncio.run(
                get_tour(
                    "https://www.songkick.com/artists/393441-dj-krush/calendar",
                    "unused-maps-key",
                    "Washington, DC",
                )
            )

        self.assertEqual(result["provider"], "songkick")
        self.assertEqual(result["parse_status"], "link_only")
        self.assertEqual(
            result["external_url"],
            "https://www.songkick.com/artists/393441-dj-krush/calendar",
        )
        read_url_mock.assert_not_called()

    def test_empty_songkick_calendar_falls_through_to_bandsintown_events(self) -> None:
        with patch(
            "finder.get_songkick_tour_info",
            new=AsyncMock(return_value=empty_songkick_payload()),
        ), patch(
            "finder.get_bandsintown_tour_info_by_name",
            new=AsyncMock(return_value=sample_bandsintown_payload()),
        ) as bandsintown_mock, patch(
            "finder.enrich_concert",
            new=AsyncMock(side_effect=lambda concert, *_args: concert),
        ), patch("finder.read_url") as read_url_mock:
            result = asyncio.run(
                get_tour(
                    "https://www.songkick.com/artists/7286084-anderson-paak",
                    "unused-maps-key",
                    "Washington, DC",
                )
            )

        self.assertEqual(result["provider"], "bandsintown")
        self.assertEqual(result["parse_status"], "full")
        self.assertEqual(result["artist_name"], "Anderson .Paak")
        self.assertEqual(len(result["concerts"]), 2)
        bandsintown_mock.assert_awaited_once_with("Anderson .Paak")
        read_url_mock.assert_not_called()

    def test_tour_provider_relations_rank_before_social_links(self) -> None:
        relations = [
            {
                "type": "social network",
                "url": {"resource": "https://twitter.com/xboygeniusx"},
            },
            {
                "type": "bandcamp",
                "url": {"resource": "https://xboygeniusx.bandcamp.com/"},
            },
            {
                "type": "bandsintown",
                "url": {"resource": "https://www.bandsintown.com/a/1493395"},
            },
            {
                "type": "official homepage",
                "url": {"resource": "https://www.xboygeniusx.com/"},
            },
        ]

        urls = ranked_artist_urls(relations)

        self.assertEqual(
            [candidate["url"] for candidate in urls],
            [
                "https://www.xboygeniusx.com/",
                "https://www.bandsintown.com/a/1493395",
                "https://xboygeniusx.bandcamp.com/",
                "https://twitter.com/xboygeniusx",
            ],
        )

    def test_official_tour_attempt_is_inserted_second(self) -> None:
        candidates = [
            {"url": "https://www.artist.com/", "type": "official homepage"},
            {"url": "https://www.bandsintown.com/a/123", "type": "bandsintown"},
        ]

        probed = insert_official_tour_attempt(candidates)

        self.assertEqual(
            [candidate["url"] for candidate in probed],
            [
                "https://www.artist.com/",
                "https://www.artist.com/tour",
                "https://www.bandsintown.com/a/123",
            ],
        )

    def test_official_tour_attempt_skipped_without_official_homepage(self) -> None:
        candidates = [
            {"url": "https://www.bandsintown.com/a/123", "type": "bandsintown"},
        ]

        self.assertEqual(insert_official_tour_attempt(candidates), candidates)

    def test_official_tour_attempt_not_duplicated(self) -> None:
        candidates = [
            {"url": "https://www.artist.com/", "type": "official homepage"},
            {"url": "https://www.artist.com/tour", "type": "official homepage"},
        ]

        self.assertEqual(insert_official_tour_attempt(candidates), candidates)

    def test_no_missing_provider_placeholders(self) -> None:
        missing = [entry for entry in self.manifest if entry.get("status") == "not_found"]
        self.assertEqual(missing, [])

    def test_ticket_urls_extracted_from_full_fixtures(self) -> None:
        expectations = {
            "Gregory Alan Isakov": ("ticketmaster.com", True),
            "Billy Strings": ("ticketmaster.com", True),
            "Jacob Collier": ("bandsintown.com", True),
            "Anoushka Lucas": ("dice.fm/event", True),
        }

        for entry in self.fixtures:
            if entry["artist_name"] not in expectations:
                continue
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            parsed = parse_tour_page_html(html, provider=entry.get("detected_provider") or entry["provider"])
            concerts = parsed["concerts"]
            fragment, require_all = expectations[entry["artist_name"]]

            with self.subTest(artist=entry["artist_name"]):
                ticketed = [concert for concert in concerts if concert.ticket_url and fragment in concert.ticket_url]
                self.assertTrue(ticketed, msg=f"Expected at least one concert with {fragment} ticket_url")
                if require_all and entry["artist_name"] == "Jacob Collier":
                    self.assertTrue(
                        all(concert.ticket_url and "bandsintown.com" in concert.ticket_url for concert in concerts),
                        msg="Jacob Collier fixture should attach Bandsintown ticket links to every row.",
                    )
                if entry["artist_name"] == "Billy Strings":
                    available = [concert for concert in concerts if not concert.is_sold_out]
                    self.assertTrue(
                        all(concert.ticket_url for concert in available),
                        msg="Non-sold-out Billy Strings rows should include a Tickets link.",
                    )

    def test_seated_event_builds_go_seated_tour_event_url(self) -> None:
        concert = Concert.from_seated_event(
            "artist-id",
            {
                "venue-name": "Venue",
                "formatted-address": "City",
                "starts-at-date-local": "2026-08-01",
                "ends-at-date-local": None,
                "is-sold-out": False,
            },
            event_id="event-uuid",
        )
        self.assertEqual(concert.ticket_url, "https://go.seated.com/tour-events/event-uuid")

    def test_seated_event_page_precedes_vip_only_url(self) -> None:
        concert = Concert.from_seated_event(
            "artist-id",
            {
                "venue-name": "Venue",
                "formatted-address": "City",
                "starts-at-date-local": "2026-08-01",
                "ends-at-date-local": None,
                "is-sold-out": False,
                "vip-link-url": "https://example.com/vip-only",
            },
            event_id="event-uuid",
        )
        self.assertEqual(concert.ticket_url, "https://go.seated.com/tour-events/event-uuid")

    def test_seated_event_prefers_exchange_listing_url(self) -> None:
        concert = Concert.from_seated_event(
            "artist-id",
            {
                "venue-name": "Venue",
                "formatted-address": "City",
                "starts-at-date-local": "2026-08-01",
                "ends-at-date-local": None,
                "is-sold-out": False,
                "exchange-listing-url": "https://example.com/resale",
            },
            event_id="event-uuid",
        )
        self.assertEqual(concert.ticket_url, "https://example.com/resale")

    def test_link_only_for_partial_fixtures(self) -> None:
        partial_expected = {
            ("bandsintown", "The War on Drugs"),
            ("bandsintown", "The Killers"),
            ("dice", "Hannah Grae"),
            ("songkick", "Job Alone & Friends"),
            ("squarespace-events", "Death Cab for Cutie"),
            ("squarespace-events", "Maggie Rogers"),
        }

        for entry in self.fixtures:
            key = (entry["provider"], entry["artist_name"])
            if key not in partial_expected:
                continue
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            provider = entry.get("detected_provider") or entry["provider"]
            parsed = parse_tour_page_html(html, provider=provider)
            result = build_tour_result(
                html=html,
                provider=provider,
                artist_url=f"https://example.com/{provider}",
                concerts=parsed["concerts"],
                artist_name=parsed["artist_name"],
                image_url=parsed["image_url"],
            )

            with self.subTest(provider=entry["provider"], artist=entry["artist_name"]):
                self.assertEqual(result["parse_status"], "link_only")
                self.assertTrue(result["external_url"])
                self.assertEqual(result["concerts"], [])

    def test_seated_partial_fixture_stays_full_parse_status(self) -> None:
        seated_partials = {
            ("seated", "Big Thief"),
            ("seated", "Wet Leg"),
        }
        for entry in self.fixtures:
            key = (entry["provider"], entry["artist_name"])
            if key not in seated_partials:
                continue
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            parsed = parse_tour_page_html(html, provider="seated")
            result = build_tour_result(
                html=html,
                provider="seated",
                artist_url="https://example.com/seated",
                concerts=parsed["concerts"],
                artist_name=parsed["artist_name"],
                image_url=parsed["image_url"],
            )
            with self.subTest(artist=entry["artist_name"]):
                self.assertEqual(result["parse_status"], "full")
                self.assertGreaterEqual(len(result["concerts"]), 1)

    def test_full_parse_still_returns_concerts(self) -> None:
        full_expected = ("Gregory Alan Isakov", "Billy Strings", "Jacob Collier")

        for entry in self.fixtures:
            if entry["artist_name"] not in full_expected:
                continue
            html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
            provider = entry.get("detected_provider") or entry["provider"]
            parsed = parse_tour_page_html(html, provider=provider)
            result = build_tour_result(
                html=html,
                provider=provider,
                artist_url=f"https://example.com/{provider}",
                concerts=parsed["concerts"],
                artist_name=parsed["artist_name"],
                image_url=parsed["image_url"],
            )

            with self.subTest(artist=entry["artist_name"]):
                self.assertEqual(result["parse_status"], "full")
                self.assertGreater(len(result["concerts"]), 0)

    def test_placeholder_attaches_ticket_href(self) -> None:
        entry = next(
            entry for entry in self.fixtures if entry["artist_name"] == "Hannah Grae" and entry["provider"] == "dice"
        )
        html = (ROOT / entry["filename"]).read_text(encoding="utf-8", errors="replace")
        parsed = parse_tour_page_html(html, provider="dice")
        result = build_tour_result(
            html=html,
            provider="dice",
            artist_url="https://example.com/dice",
            concerts=parsed["concerts"],
            artist_name=parsed["artist_name"],
            image_url=parsed["image_url"],
        )

        self.assertEqual(result["parse_status"], "link_only")
        self.assertIn("dice.fm/event", result["external_url"])


if __name__ == "__main__":
    unittest.main()
