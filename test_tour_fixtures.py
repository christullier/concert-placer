import json
import unittest
from pathlib import Path

from finder import (
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


if __name__ == "__main__":
    unittest.main()
