import importlib.util
import logging
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_radio_module():
    spec = importlib.util.spec_from_file_location(
        "dr_podcasts_radio", ROOT / "dr_podcasts" / "radio.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


radio = load_radio_module()
HTML = (ROOT / "tests" / "fixtures" / "radio_page.html").read_text(encoding="utf-8")
CHANNELS = radio.CHANNELS_BY_SLUG
LOGGER_NAME = "dr_podcasts_test"
STREAM = "https://drliveradio2.akamaized.net/hls/live/2118698/{}/masterab.m3u8"
MOVED_P3 = "https://drliveradio3.akamaized.net/hls/live/2200001/p3/masterab.m3u8"


def slugs(channels):
    return [channel.slug for channel in channels]


class ChannelsTest(unittest.TestCase):
    def test_the_national_channels_in_order(self):
        self.assertEqual(
            [channel.name for channel in radio.CHANNELS],
            [
                "DR P1",
                "DR P2",
                "DR P3",
                "DR P4 København",
                "DR P5 København",
                "DR P6 Beat",
                "DR P8 Jazz",
            ],
        )

    def test_slugs_are_unique(self):
        self.assertEqual(len(CHANNELS), len(radio.CHANNELS))

    def test_urls(self):
        p4 = CHANNELS["p4kbh"]
        self.assertEqual(p4.page_url, "https://www.dr.dk/lyd/p4kbh")
        self.assertEqual(p4.fallback_stream_url, STREAM.format("p4kobenhavn"))
        self.assertEqual(p4.logo_url, "https://www.dr.dk/lyd/_next/static/media/p4.a9a465ed.svg")


class FindStreamUrlTest(unittest.TestCase):
    def test_picks_the_channel_out_of_a_page_that_lists_every_channel(self):
        self.assertEqual(radio.find_stream_url(HTML, CHANNELS["p3"]), STREAM.format("p3"))
        self.assertEqual(radio.find_stream_url(HTML, CHANNELS["p8jazz"]), STREAM.format("p8"))

    def test_uses_the_stream_slug_rather_than_the_page_slug(self):
        self.assertEqual(
            radio.find_stream_url(HTML, CHANNELS["p4kbh"]), STREAM.format("p4kobenhavn")
        )
        self.assertEqual(
            radio.find_stream_url(HTML, CHANNELS["p5kbh"]), STREAM.format("p5kobenhavn")
        )
        self.assertEqual(radio.find_stream_url(HTML, CHANNELS["p6beat"]), STREAM.format("p6"))

    def test_follows_a_new_host_and_id(self):
        html = HTML.replace(STREAM.format("p3"), MOVED_P3)
        self.assertEqual(radio.find_stream_url(html, CHANNELS["p3"]), MOVED_P3)

    def test_matches_whole_slugs_only(self):
        html = f'"{STREAM.format("p10")}" "{STREAM.format("xp1")}" "{STREAM.format("p1x")}"'
        self.assertIsNone(radio.find_stream_url(html, CHANNELS["p1"]))

    def test_none_when_the_page_lacks_the_channel(self):
        self.assertIsNone(radio.find_stream_url(HTML, CHANNELS["p2"]))

    def test_none_for_a_page_without_streams(self):
        html = "<html><body>Maintenance</body></html>"
        self.assertIsNone(radio.find_stream_url(html, CHANNELS["p3"]))


class SearchChannelsTest(unittest.TestCase):
    def test_matches_the_name_ignoring_case(self):
        self.assertEqual(slugs(radio.search_channels("dr P3", 10)), ["p3"])
        self.assertEqual(slugs(radio.search_channels("jazz", 10)), ["p8jazz"])

    def test_every_word_must_match(self):
        self.assertEqual(slugs(radio.search_channels("københavn", 10)), ["p4kbh", "p5kbh"])
        self.assertEqual(slugs(radio.search_channels("p4 københavn", 10)), ["p4kbh"])
        self.assertEqual(radio.search_channels("p3 jazz", 10), [])

    def test_limit(self):
        self.assertEqual(len(radio.search_channels("dr", 3)), 3)

    def test_blank_query_matches_nothing(self):
        self.assertEqual(radio.search_channels("  ", 10), [])


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Pages:
    """Stands in for the dr.dk/lyd pages."""

    def __init__(self):
        self.html = HTML
        self.error = None
        self.requests = []

    async def __call__(self, url):
        self.requests.append(url)
        if self.error:
            raise radio.StreamPageError(self.error)
        return self.html


class StreamResolverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.pages = Pages()
        self.clock = Clock()
        self.resolver = radio.StreamResolver(self.pages, logging.getLogger(LOGGER_NAME), self.clock)

    async def test_reads_the_url_from_the_channel_page(self):
        url = await self.resolver.resolve(CHANNELS["p4kbh"])
        self.assertEqual(url, STREAM.format("p4kobenhavn"))
        self.assertEqual(self.pages.requests, ["https://www.dr.dk/lyd/p4kbh"])

    async def test_keeps_the_url_for_twelve_hours(self):
        await self.resolver.resolve(CHANNELS["p3"])
        self.clock.now += radio.STREAM_CACHE_SECONDS - 1
        self.assertEqual(await self.resolver.resolve(CHANNELS["p3"]), STREAM.format("p3"))
        self.assertEqual(len(self.pages.requests), 1)
        self.pages.html = HTML.replace(STREAM.format("p3"), MOVED_P3)
        self.clock.now += 1
        self.assertEqual(await self.resolver.resolve(CHANNELS["p3"]), MOVED_P3)
        self.assertEqual(len(self.pages.requests), 2)

    async def test_channels_are_kept_apart(self):
        await self.resolver.resolve(CHANNELS["p3"])
        self.assertEqual(await self.resolver.resolve(CHANNELS["p6beat"]), STREAM.format("p6"))
        self.assertEqual(len(self.pages.requests), 2)

    async def test_unreadable_page_gives_the_fixed_url_when_no_url_was_found_before(self):
        self.pages.error = "Could not load the page"
        with self.assertLogs(LOGGER_NAME, "WARNING") as logs:
            url = await self.resolver.resolve(CHANNELS["p3"])
        self.assertEqual(url, CHANNELS["p3"].fallback_stream_url)
        self.assertIn("Could not load the page", logs.output[0])

    async def test_page_without_the_channel_gives_the_fixed_url(self):
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            url = await self.resolver.resolve(CHANNELS["p2"])
        self.assertEqual(url, STREAM.format("p2"))

    async def test_unreadable_page_gives_the_last_url_that_was_found(self):
        self.pages.html = HTML.replace(STREAM.format("p3"), MOVED_P3)
        await self.resolver.resolve(CHANNELS["p3"])
        self.clock.now += radio.STREAM_CACHE_SECONDS
        self.pages.error = "Could not load the page"
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            url = await self.resolver.resolve(CHANNELS["p3"])
        self.assertEqual(url, MOVED_P3)

    async def test_retries_an_unreadable_page_after_ten_minutes(self):
        self.pages.error = "Could not load the page"
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            await self.resolver.resolve(CHANNELS["p3"])
            self.clock.now += radio.STREAM_RETRY_SECONDS - 1
            await self.resolver.resolve(CHANNELS["p3"])
        self.assertEqual(len(self.pages.requests), 1)
        self.pages.error = None
        self.pages.html = HTML.replace(STREAM.format("p3"), MOVED_P3)
        self.clock.now += 1
        self.assertEqual(await self.resolver.resolve(CHANNELS["p3"]), MOVED_P3)
        self.assertEqual(len(self.pages.requests), 2)


if __name__ == "__main__":
    unittest.main()
