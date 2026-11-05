import asyncio
import copy
import importlib.util
import json
import logging
import sys
import unittest
from datetime import datetime
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
FIXTURES = ROOT / "tests" / "fixtures"
HTML = (FIXTURES / "radio_page.html").read_text(encoding="utf-8")
TRACKS_P3 = json.loads((FIXTURES / "radio_tracks_p3.json").read_text(encoding="utf-8"))
TRACKS_P2 = json.loads((FIXTURES / "radio_tracks_p2.json").read_text(encoding="utf-8"))
PROGRAMMES = json.loads((FIXTURES / "radio_programmes.json").read_text(encoding="utf-8"))
CHANNELS = radio.CHANNELS_BY_SLUG
LOGGER_NAME = "dr_podcasts_test"
STREAM = "https://drliveradio2.akamaized.net/hls/live/2118698/{}/masterab.m3u8"
MOVED_P3 = "https://drliveradio3.akamaized.net/hls/live/2200001/p3/masterab.m3u8"
P3_PICTURE = (
    "https://asset.dr.dk/drlyd/images/urn:dr:radio:image:6ad5c1091068ba26db3ae6da"
    "?im=AspectCrop%3D%28512%2C512%29%2CxPosition%3D.5%2CyPosition%3D.5%3BResize%3D%28512%2C512%29"
)


def epoch(time_of_day):
    """Return the epoch seconds of a time on the day of the captured fixtures."""
    return datetime.fromisoformat(f"2026-11-05T{time_of_day}+00:00").timestamp()


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
        self.assertEqual(
            p4.tracks_url,
            "https://api.dr.dk/radio/v5/indexpoints/live/"
            "urn:dr:radio:channel:5fa156d3da351264f87b4651",
        )

    def test_every_channel_has_a_track_list_of_its_own(self):
        urls = {channel.tracks_url for channel in radio.CHANNELS}
        self.assertEqual(len(urls), len(radio.CHANNELS))


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


def track_item(**changes):
    item = {
        "type": "Track",
        "title": "Raindance",
        "playedTime": "2026-11-05T22:03:09+00:00",
        "durationMilliseconds": 205000,
        "classical": False,
        "description": "Dave feat. Tems",
        "roles": [],
    }
    return {**item, **changes}


def parsed_titles(document):
    return [track.title for track in radio.parse_tracks(document)]


class ParseTracksTest(unittest.TestCase):
    def test_a_track(self):
        track = radio.parse_tracks(TRACKS_P3)[0]
        self.assertEqual(
            track,
            radio.Track("Min egen værste ven", "Friða Saiya", epoch("22:28:36"), epoch("22:31:30")),
        )

    def test_every_track_of_the_list(self):
        self.assertEqual(len(radio.parse_tracks(TRACKS_P3)), 12)

    def test_the_artist_line_names_the_guests(self):
        tracks = {track.title: track for track in radio.parse_tracks(TRACKS_P3)}
        self.assertEqual(tracks["Raindance"].artist, "Dave feat. Tems")

    def test_classical_music_names_the_composers(self):
        tracks = {track.title: track for track in radio.parse_tracks(TRACKS_P2)}
        self.assertEqual(tracks["Ave Maris Stella"].artist, "Claudio Monteverdi")
        self.assertEqual(tracks["Suor Angelica - Intermezzo"].artist, "Giacomo Puccini")

    def test_each_composer_is_named_once(self):
        roles = [
            {"role": "Komponist", "name": "Carl Nielsen"},
            {"role": "Dirigent", "name": "Bo Holten"},
            {"role": "komponist", "name": "Wayne Siegel"},
            {"role": "Komponist", "name": "Carl Nielsen"},
        ]
        [track] = radio.parse_tracks({"items": [track_item(classical=True, roles=roles)]})
        self.assertEqual(track.artist, "Carl Nielsen, Wayne Siegel")

    def test_classical_music_without_a_composer_keeps_the_credits(self):
        tracks = radio.parse_tracks(TRACKS_P2)
        track = next(track for track in tracks if track.title.startswith("La peau douce"))
        self.assertTrue(track.artist.startswith("KOMPONIST: Georges Delerue."))

    def test_other_music_on_a_classical_channel_keeps_the_artist_line(self):
        tracks = {track.title: track for track in radio.parse_tracks(TRACKS_P2)}
        self.assertEqual(tracks["Kære fisk"].artist, "Ursula Andkjær Olsen")

    def test_no_artist_line_gives_no_artist(self):
        for description in (None, "", "  "):
            [track] = radio.parse_tracks({"items": [track_item(description=description)]})
            self.assertIsNone(track.artist)

    def test_leaves_out_what_is_not_a_complete_track(self):
        items = [
            track_item(title="Good"),
            track_item(title="A chapter", type="Chapter"),
            track_item(title=None),
            track_item(title="  "),
            track_item(title="No start", playedTime=None),
            track_item(title="Start without offset", playedTime="2026-11-05T22:03:09"),
            track_item(title="Start in words", playedTime="a while ago"),
            track_item(title="No duration", durationMilliseconds=None),
            track_item(title="Empty duration", durationMilliseconds=0),
            track_item(title="Duration in words", durationMilliseconds="205000"),
        ]
        self.assertEqual(parsed_titles({"items": items}), ["Good"])

    def test_unreadable_documents_have_no_tracks(self):
        for document in (None, [], "text", 5, {}, {"items": None}, {"items": "x"}):
            self.assertEqual(radio.parse_tracks(document), [])
        self.assertEqual(radio.parse_tracks({"items": [None, 5, "x", []]}), [])


class FindTrackTest(unittest.TestCase):
    def setUp(self):
        self.tracks = radio.parse_tracks(TRACKS_P3)

    def title_at(self, time_of_day):
        track = radio.find_track(self.tracks, epoch(time_of_day))
        return track.title if track else None

    def test_the_track_playing(self):
        self.assertEqual(self.title_at("22:30:00"), "Min egen værste ven")
        self.assertEqual(self.title_at("22:26:00"), "DtMF")

    def test_a_track_plays_from_its_start_until_its_end(self):
        self.assertEqual(self.title_at("22:28:35"), "DtMF")
        self.assertEqual(self.title_at("22:28:36"), "Min egen værste ven")
        self.assertEqual(self.title_at("22:31:29"), "Min egen værste ven")
        self.assertIsNone(self.title_at("22:31:30"))

    def test_the_latest_track_wins_when_it_starts_before_the_last_one_is_over(self):
        self.assertEqual(self.title_at("22:29:00"), "Min egen værste ven")

    def test_none_between_tracks(self):
        self.assertIsNone(self.title_at("22:24:00"))

    def test_none_before_the_oldest_track_and_without_tracks(self):
        self.assertIsNone(self.title_at("21:00:00"))
        self.assertIsNone(radio.find_track([], epoch("22:30:00")))


class FindProgrammeTest(unittest.TestCase):
    def find(self, slug, time_of_day="22:30:00", document=PROGRAMMES):
        return radio.find_programme(document, CHANNELS[slug], epoch(time_of_day))

    def test_the_programme_on_air(self):
        programme = self.find("p3")
        self.assertEqual(programme.title, "Den sidste time")
        self.assertEqual(programme.end, epoch("23:03:00"))
        self.assertEqual(self.find("p2").title, "Natsværmeren: Poesi")

    def test_the_picture_is_the_square_one_of_the_programme(self):
        self.assertEqual(self.find("p3").image_url, P3_PICTURE)

    def test_the_square_image_is_preferred_to_the_other_square_ones(self):
        document = copy.deepcopy(PROGRAMMES)
        entry = next(entry for entry in document if entry["channel"]["slug"] == "p3")
        entry["imageAssets"] = [
            {"id": "urn:dr:radio:image:a", "target": "Podcast", "ratio": "1:1"},
            {"id": "urn:dr:radio:image:b", "target": "SquareImage", "ratio": "1:1"},
            {"id": "urn:dr:radio:image:c", "target": "Default", "ratio": "16:9"},
        ]
        picture = self.find("p3", document=document).image_url
        self.assertEqual(picture, radio.IMAGE_URL.format("urn:dr:radio:image:b"))

    def test_no_picture_without_a_square_one(self):
        document = copy.deepcopy(PROGRAMMES)
        entry = next(entry for entry in document if entry["channel"]["slug"] == "p3")
        entry["imageAssets"] = [asset for asset in entry["imageAssets"] if asset["ratio"] != "1:1"]
        self.assertIsNone(self.find("p3", document=document).image_url)
        del entry["imageAssets"]
        self.assertIsNone(self.find("p3", document=document).image_url)

    def test_the_channel_is_picked_by_its_whole_slug(self):
        document = copy.deepcopy(PROGRAMMES)
        for entry in document:
            if entry["channel"]["slug"] == "p4bornholm":
                entry["title"] = "Bornholm"
        self.assertEqual(self.find("p4kbh", document=document).title, "Før natten på P4")

    def test_none_once_the_programme_is_over(self):
        self.assertIsNotNone(self.find("p3", "23:02:59"))
        self.assertIsNone(self.find("p3", "23:03:00"))

    def test_none_for_a_channel_that_is_not_listed(self):
        self.assertIsNone(self.find("p8jazz"))

    def test_none_for_a_programme_without_a_title_or_an_end(self):
        for change in ({"title": None}, {"title": " "}, {"endTime": None}, {"endTime": "late"}):
            document = copy.deepcopy(PROGRAMMES)
            next(e for e in document if e["channel"]["slug"] == "p3").update(change)
            self.assertIsNone(self.find("p3", document=document), change)

    def test_none_for_unreadable_documents(self):
        for document in (None, {}, "text", [None, 5, "x", {"channel": None}, {"channel": []}]):
            self.assertIsNone(self.find("p3", document=document))


class Documents:
    """Stands in for the DR radio API."""

    def __init__(self):
        self.documents = {
            CHANNELS["p3"].tracks_url: TRACKS_P3,
            CHANNELS["p2"].tracks_url: TRACKS_P2,
            CHANNELS["p8jazz"].tracks_url: {"items": []},
            radio.PROGRAMMES_URL: PROGRAMMES,
        }
        self.errors = {}
        self.requests = []

    async def __call__(self, url):
        self.requests.append(url)
        await asyncio.sleep(0)
        if url in self.errors:
            raise radio.NowPlayingError(self.errors[url])
        return self.documents[url]

    def fail(self, *urls):
        self.errors = {url: "Could not load the document" for url in urls}


class NowPlayingResolverTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = Documents()
        self.clock = Clock()
        self.wall = Clock()
        self.listening_at("22:30:00")
        self.resolver = radio.NowPlayingResolver(
            self.api, logging.getLogger(LOGGER_NAME), self.clock, self.wall
        )

    def listening_at(self, time_of_day):
        """Set the wall clock so that what the listener hears is what DR played at the time."""
        self.wall.now = epoch(time_of_day) + radio.STREAM_DELAY_SECONDS

    async def resolve(self, slug="p3"):
        return await self.resolver.resolve(CHANNELS[slug])

    async def test_the_track_playing_with_the_picture_of_the_programme(self):
        self.assertEqual(
            await self.resolve(),
            radio.NowPlaying("Min egen værste ven", "Friða Saiya", P3_PICTURE),
        )

    async def test_classical_music(self):
        self.listening_at("22:20:00")
        playing = await self.resolve("p2")
        self.assertEqual(playing.title, "Suor Angelica - Intermezzo")
        self.assertEqual(playing.artist, "Giacomo Puccini")

    async def test_the_track_follows_what_the_listener_hears(self):
        self.wall.now = epoch("22:28:36") + radio.STREAM_DELAY_SECONDS - 1
        self.assertEqual((await self.resolve()).title, "DtMF")
        self.wall.now += 1
        self.assertEqual((await self.resolve()).title, "Min egen værste ven")

    async def test_the_programme_between_tracks(self):
        self.listening_at("22:24:00")
        self.assertEqual(
            await self.resolve(), radio.NowPlaying("Den sidste time", None, P3_PICTURE)
        )

    async def test_the_programme_when_the_tracks_cannot_be_loaded(self):
        self.api.fail(CHANNELS["p3"].tracks_url)
        with self.assertLogs(LOGGER_NAME, "DEBUG") as logs:
            playing = await self.resolve()
        self.assertEqual(playing, radio.NowPlaying("Den sidste time", None, P3_PICTURE))
        self.assertIn("Could not load the document", logs.output[0])
        self.assertEqual({record.levelno for record in logs.records}, {logging.DEBUG})

    async def test_the_track_without_a_picture_when_the_programmes_cannot_be_loaded(self):
        self.api.fail(radio.PROGRAMMES_URL)
        self.assertEqual(
            await self.resolve(), radio.NowPlaying("Min egen værste ven", "Friða Saiya", None)
        )

    async def test_the_channel_when_nothing_can_be_loaded(self):
        self.api.fail(CHANNELS["p3"].tracks_url, radio.PROGRAMMES_URL)
        self.assertEqual(await self.resolve(), radio.NowPlaying("DR P3"))

    async def test_the_channel_when_the_documents_make_no_sense(self):
        self.api.documents = {
            CHANNELS["p3"].tracks_url: {"items": "none"},
            radio.PROGRAMMES_URL: {"programmes": []},
        }
        self.assertEqual(await self.resolve(), radio.NowPlaying("DR P3"))

    async def test_the_channel_when_it_has_neither_tracks_nor_a_programme(self):
        self.assertEqual(await self.resolve("p8jazz"), radio.NowPlaying("DR P8 Jazz"))

    async def test_the_last_documents_serve_until_their_times_have_passed(self):
        await self.resolve()
        self.api.fail(CHANNELS["p3"].tracks_url, radio.PROGRAMMES_URL)
        self.clock.now += radio.NOW_PLAYING_CACHE_SECONDS
        self.listening_at("22:31:29")
        self.assertEqual((await self.resolve()).title, "Min egen værste ven")
        self.clock.now += radio.NOW_PLAYING_CACHE_SECONDS
        self.listening_at("22:31:30")
        self.assertEqual(
            await self.resolve(), radio.NowPlaying("Den sidste time", None, P3_PICTURE)
        )
        self.clock.now += radio.NOW_PLAYING_CACHE_SECONDS
        self.listening_at("23:03:00")
        self.assertEqual(await self.resolve(), radio.NowPlaying("DR P3"))

    async def test_recovers_when_the_documents_can_be_loaded_again(self):
        self.api.fail(CHANNELS["p3"].tracks_url, radio.PROGRAMMES_URL)
        self.assertEqual(await self.resolve(), radio.NowPlaying("DR P3"))
        self.api.fail()
        self.clock.now += radio.NOW_PLAYING_CACHE_SECONDS
        self.assertEqual((await self.resolve()).title, "Min egen værste ven")

    async def test_reads_each_document_once_per_half_minute(self):
        await self.resolve()
        self.assertEqual(self.api.requests, [CHANNELS["p3"].tracks_url, radio.PROGRAMMES_URL])
        self.clock.now += radio.NOW_PLAYING_CACHE_SECONDS - 1
        await self.resolve()
        self.assertEqual(len(self.api.requests), 2)
        self.clock.now += 1
        await self.resolve()
        self.assertEqual(len(self.api.requests), 4)

    async def test_does_not_retry_a_failed_document_before_the_half_minute_is_over(self):
        self.api.fail(CHANNELS["p3"].tracks_url, radio.PROGRAMMES_URL)
        await self.resolve()
        self.clock.now += radio.NOW_PLAYING_CACHE_SECONDS - 1
        await self.resolve()
        self.assertEqual(len(self.api.requests), 2)
        self.clock.now += 1
        await self.resolve()
        self.assertEqual(len(self.api.requests), 4)

    async def test_the_programmes_are_shared_between_channels(self):
        await self.resolve("p3")
        await self.resolve("p2")
        self.assertEqual(
            self.api.requests,
            [CHANNELS["p3"].tracks_url, radio.PROGRAMMES_URL, CHANNELS["p2"].tracks_url],
        )

    async def test_listeners_asking_together_cause_one_request_per_document(self):
        await asyncio.gather(*(self.resolve() for _ in range(5)))
        self.assertEqual(len(self.api.requests), 2)


if __name__ == "__main__":
    unittest.main()
