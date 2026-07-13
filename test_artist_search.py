import unittest
from unittest.mock import AsyncMock, patch

from finder import search_musicbrainz_artists


class ArtistSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_promotes_exact_short_name_from_larger_candidate_pool(self):
        candidates = [
            {
                "id": f"other-{index}",
                "name": f"Artist V {index}",
                "score": 100 - index,
            }
            for index in range(10)
        ]
        candidates.append(
            {
                "id": "83096042-3785-481e-8843-dee69f1aad12",
                "name": "V",
                "disambiguation": "BTS",
                "country": "KR",
                "score": 84,
            }
        )

        with patch(
            "finder.read_musicbrainz_json_async",
            new=AsyncMock(return_value={"artists": candidates}),
        ) as read_musicbrainz:
            results = await search_musicbrainz_artists("V", limit=8)

        self.assertEqual(results[0]["name"], "V")
        self.assertEqual(results[0]["disambiguation"], "BTS")
        self.assertEqual(len(results), 8)
        self.assertIn("limit=25", read_musicbrainz.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
