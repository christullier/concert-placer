import json
import unittest
from pathlib import Path

from Concert import Concert
from finder import (
    build_tour_result,
    detect_tour_provider,
    get_artist_id_from_html,
    insert_official_tour_attempt,
    parse_tour_page_html,
    ranked_artist_urls,
)

ROOT = Path(__file__).resolve().parent
FIXTURES_DIR = ROOT / "fixtures" / "tour-pages"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"


def load_manifest() -> list[dict]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def saved_fixtures(manifest: list[dict]) -> list[dict]:
    return [entry for entry in manifest if entry.get("status") != "not_found"]


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
