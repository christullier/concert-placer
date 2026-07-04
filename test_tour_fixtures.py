import json
import unittest
from pathlib import Path

from finder import detect_tour_provider, get_artist_id_from_html

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

    def test_no_missing_provider_placeholders(self) -> None:
        missing = [entry for entry in self.manifest if entry.get("status") == "not_found"]
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
