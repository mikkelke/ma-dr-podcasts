import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_index_module():
    spec = importlib.util.spec_from_file_location(
        "dr_podcasts_index", ROOT / "dr_podcasts" / "index.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


index = load_index_module()
HTML = (ROOT / "tests" / "fixtures" / "index.html").read_text(encoding="utf-8")
ENTRIES = index.parse_index(HTML)


def slugs(entries):
    return [entry.slug for entry in entries]


class ParseIndexTest(unittest.TestCase):
    def test_skips_rows_without_podcast_data(self):
        self.assertEqual(len(ENTRIES), 7)

    def test_entry_fields(self):
        genstart = next(entry for entry in ENTRIES if entry.slug == "genstart")
        self.assertEqual(genstart.title, "Genstart")
        self.assertEqual(genstart.episodes, 1519)
        self.assertEqual(genstart.latest, "2026-10-15 03:00")
        self.assertTrue(genstart.description.startswith("DR's nyhedspodcast.\nTidens"))
        self.assertEqual(genstart.feed_url, "https://drpodcast.nu/genstart/feed.xml")
        self.assertEqual(genstart.thumb_url, "https://drpodcast.nu/genstart/thumb.jpg")

    def test_unescapes_html_entities(self):
        sara = next(entry for entry in ENTRIES if entry.slug == "sara-og-monopolet")
        self.assertEqual(sara.title, "Sara & Monopolet - podcast")

    def test_no_rows_gives_no_entries(self):
        self.assertEqual(index.parse_index("<html><body>Maintenance</body></html>"), [])


class SearchIndexTest(unittest.TestCase):
    def test_title_match_ignores_case(self):
        self.assertEqual(slugs(index.search_index(ENTRIES, "GENstart", 10)), ["genstart"])

    def test_matches_description(self):
        self.assertEqual(slugs(index.search_index(ENTRIES, "nyhedspodcast", 10)), ["genstart"])

    def test_every_word_must_match(self):
        self.assertEqual(
            slugs(index.search_index(ENTRIES, "sara monopolet", 10)), ["sara-og-monopolet"]
        )
        self.assertEqual(index.search_index(ENTRIES, "sara genstart", 10), [])

    def test_words_may_be_spread_over_title_and_description(self):
        self.assertEqual(
            slugs(index.search_index(ENTRIES, "genstart nyhedshistorier", 10)), ["genstart"]
        )

    def test_title_matches_rank_before_description_matches(self):
        self.assertEqual(
            slugs(index.search_index(ENTRIES, "nyheder", 10)),
            ["p4-midt-og-vest-regionale-nyheder", "radioavisen"],
        )

    def test_prefix_of_title_ranks_first(self):
        self.assertEqual(
            slugs(index.search_index(ENTRIES, "s", 10))[:2],
            ["sara-og-monopolet", "bangers-med-ena"],
        )

    def test_limit(self):
        self.assertEqual(len(index.search_index(ENTRIES, "s", 3)), 3)

    def test_blank_query_matches_nothing(self):
        self.assertEqual(index.search_index(ENTRIES, "  ", 10), [])


class RecentlyUpdatedTest(unittest.TestCase):
    def test_newest_first_without_empty_podcasts(self):
        self.assertEqual(
            slugs(index.recently_updated(ENTRIES, 3)),
            ["radioavisen", "p4-midt-og-vest-regionale-nyheder", "genstart"],
        )
        self.assertNotIn("frokostkoncerten", slugs(index.recently_updated(ENTRIES, 10)))


if __name__ == "__main__":
    unittest.main()
