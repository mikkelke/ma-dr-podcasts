import asyncio
import copy
import logging
import unittest

from support import LOGGER_NAME, Clock, FakeApi, api, epoch, fixture, radio

CHANNELS_DOCUMENT = fixture("channels.json")
TRACKS_P3 = fixture("radio_tracks_p3.json")
TRACKS_P2 = fixture("radio_tracks_p2.json")
PROGRAMMES = fixture("radio_programmes.json")
CHANNEL_LIST = radio.parse_channels(CHANNELS_DOCUMENT)
CHANNELS = {channel.slug: channel for channel in CHANNEL_LIST}
STREAM = "https://drliveradio2.akamaized.net/hls/live/2118698/{}/masterab.m3u8"
ICY_STREAM = "https://live-icy.dr.dk/A/{}.mp3"
P3_LOGO = api.IMAGE_URL.format("urn:dr:radio:image:624c02604e30a95b58edee96")
P3_PICTURE = api.IMAGE_URL.format("urn:dr:radio:image:6ad5c1091068ba26db3ae6da")
LIBRARY_SLUGS = ["p1", "p2", "p3", "p4kbh", "p5kbh", "p6beat", "p8jazz"]


def slugs(channels):
    return [channel.slug for channel in channels]


def document_with(slug, **changes):
    """Return the channels document with changes made to the channel of the slug."""
    document = copy.deepcopy(CHANNELS_DOCUMENT)
    for entry in document:
        for channel in [entry, *entry.get("districts", [])]:
            if channel["slug"] == slug:
                channel.update(changes)
    return document


def channel_item(slug="p9", **changes):
    item = {
        "slug": slug,
        "title": "P9",
        "id": f"urn:dr:radio:channel:{slug}",
        "isFlowChannel": True,
        "audioAssets": [{"format": "HLS", "url": STREAM.format(slug)}],
    }
    return {**item, **changes}


class ParseChannelsTest(unittest.TestCase):
    def test_the_flow_channels_in_the_order_of_dr(self):
        self.assertEqual(
            slugs(CHANNEL_LIST),
            ["p1", "p2", "p3", "p4kbh", "p4vest", "p4aarhus", "p5kbh", "p6beat", "p8jazz"],
        )

    def test_the_names(self):
        self.assertEqual(
            [channel.name for channel in CHANNEL_LIST],
            [
                "DR P1",
                "DR P2",
                "DR P3",
                "DR P4 København",
                "DR P4 Midt & Vest",
                "DR P4 Østjylland",
                "DR P5 København",
                "DR P6 Beat",
                "DR P8 Jazz",
            ],
        )

    def test_the_item_ids_of_earlier_versions_are_still_channels(self):
        self.assertEqual([slug for slug in LIBRARY_SLUGS if slug not in CHANNELS], [])

    def test_the_districts_stand_in_for_p4_and_p5(self):
        self.assertNotIn("p4", CHANNELS)
        self.assertNotIn("p5", CHANNELS)
        self.assertEqual(
            [channel.slug for channel in CHANNEL_LIST if channel.district],
            ["p4kbh", "p4vest", "p4aarhus", "p5kbh"],
        )

    def test_a_channel_with_districts_and_a_stream_is_listed_with_them(self):
        document = [
            channel_item("p4", districts=[channel_item("p4a"), channel_item("p4b")]),
        ]
        self.assertEqual(slugs(radio.parse_channels(document)), ["p4a", "p4b"])
        document[0]["districts"] = []
        self.assertEqual(slugs(radio.parse_channels(document)), ["p4"])

    def test_leaves_out_what_is_not_a_station(self):
        for slug in ("ly1", "p7mix", "special-radio", "mcrweb1", "dr-web-3", "p3webcam"):
            self.assertNotIn(slug, CHANNELS)

    def test_the_hls_stream_is_preferred(self):
        self.assertEqual(CHANNELS["p3"].stream_url, STREAM.format("p3"))
        self.assertTrue(CHANNELS["p3"].hls)
        self.assertEqual(CHANNELS["p4kbh"].stream_url, STREAM.format("p4kobenhavn"))
        self.assertEqual(CHANNELS["p6beat"].stream_url, STREAM.format("p6"))
        self.assertEqual(CHANNELS["p8jazz"].stream_url, STREAM.format("p8"))

    def test_the_best_icy_stream_when_there_is_no_hls_one(self):
        p3 = next(item for item in CHANNELS_DOCUMENT if item["slug"] == "p3")
        assets = [asset for asset in p3["audioAssets"] if asset["format"] != "HLS"]
        [channel] = radio.parse_channels([channel_item("p3", audioAssets=assets)])
        self.assertEqual(channel.stream_url, ICY_STREAM.format("A05H"))
        self.assertFalse(channel.hls)

    def test_leaves_out_a_channel_without_a_usable_stream(self):
        for assets in (
            None,
            [],
            "text",
            [None, 5, {}],
            [{"format": "HLS"}, {"format": "HLS", "url": " "}],
            [{"format": "HLS", "url": "rtmp://example.com/live"}],
            [{"format": "DASH", "url": STREAM.format("p9")}],
        ):
            self.assertEqual(radio.parse_channels([channel_item(audioAssets=assets)]), [], assets)

    def test_leaves_out_a_channel_without_a_slug_a_title_or_an_id(self):
        for change in ({"slug": None}, {"title": " "}, {"id": None}, {"isFlowChannel": None}):
            self.assertEqual(radio.parse_channels([channel_item(**change)]), [], change)

    def test_the_first_channel_of_a_slug_wins(self):
        document = [channel_item("p9", title="First"), channel_item("p9", title="Second")]
        self.assertEqual([channel.name for channel in radio.parse_channels(document)], ["DR First"])

    def test_each_channel_has_a_track_list_of_its_own(self):
        urls = {channel.tracks_url for channel in CHANNEL_LIST}
        self.assertEqual(len(urls), len(CHANNEL_LIST))
        self.assertEqual(
            CHANNELS["p4kbh"].tracks_url,
            "https://api.dr.dk/radio/v5/indexpoints/live/"
            "urn:dr:radio:channel:5fa156d3da351264f87b4651",
        )

    def test_the_picture_is_the_logo_else_the_square_picture(self):
        self.assertEqual(CHANNELS["p3"].image_url, P3_LOGO)
        document = document_with("p3", channelLogos=[])
        self.assertEqual(
            next(c for c in radio.parse_channels(document) if c.slug == "p3").image_url,
            api.IMAGE_URL.format("urn:dr:radio:image:6499a86d2222ea4246b8340d"),
        )
        document = document_with("p3", channelLogos=[], imageAssets=[])
        self.assertIsNone(
            next(c for c in radio.parse_channels(document) if c.slug == "p3").image_url
        )

    def test_names_follow_the_slug_where_dr_titles_a_channel_short(self):
        for slug, title, name in (
            ("p6beat", "P6", "DR P6 Beat"),
            ("p8jazz", "P8", "DR P8 Jazz"),
            ("p4kbh", "P4 København", "DR P4 København"),
            ("p3", "P3", "DR P3"),
            ("p4vest", "P4 Midt & Vest", "DR P4 Midt & Vest"),
        ):
            [channel] = radio.parse_channels([channel_item(slug, title=title)])
            self.assertEqual(channel.name, name)

    def test_unreadable_documents_have_no_channels(self):
        for document in (None, {}, "text", 5, [], [None, 5, "x", []], [{"districts": "x"}]):
            self.assertEqual(radio.parse_channels(document), [])


class SearchChannelsTest(unittest.TestCase):
    def test_matches_the_name_ignoring_case(self):
        self.assertEqual(slugs(radio.search_channels(CHANNEL_LIST, "dr P3", 10)), ["p3"])
        self.assertEqual(slugs(radio.search_channels(CHANNEL_LIST, "jazz", 10)), ["p8jazz"])

    def test_every_word_must_match(self):
        self.assertEqual(
            slugs(radio.search_channels(CHANNEL_LIST, "københavn", 10)), ["p4kbh", "p5kbh"]
        )
        self.assertEqual(slugs(radio.search_channels(CHANNEL_LIST, "p4 københavn", 10)), ["p4kbh"])
        self.assertEqual(radio.search_channels(CHANNEL_LIST, "p3 jazz", 10), [])

    def test_districts_are_found_too(self):
        self.assertEqual(
            slugs(radio.search_channels(CHANNEL_LIST, "p4", 10)),
            [
                "p4kbh",
                "p4vest",
                "p4aarhus",
            ],
        )

    def test_limit(self):
        self.assertEqual(len(radio.search_channels(CHANNEL_LIST, "dr", 3)), 3)

    def test_blank_query_matches_nothing(self):
        self.assertEqual(radio.search_channels(CHANNEL_LIST, "  ", 10), [])


class ChannelDirectoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = FakeApi({radio.CHANNELS_URL: CHANNELS_DOCUMENT})
        self.clock = Clock()
        self.directory = radio.ChannelDirectory(
            self.api, logging.getLogger(LOGGER_NAME), self.clock
        )

    async def test_reads_the_channels_of_dr(self):
        self.assertEqual(slugs(await self.directory.channels()), slugs(CHANNEL_LIST))
        self.assertEqual(self.api.requests, ["https://api.dr.dk/radio/v5/channels"])

    async def test_finds_a_channel_by_its_slug(self):
        channel = await self.directory.find("p3")
        self.assertEqual((channel.slug, channel.name), ("p3", "DR P3"))
        self.assertIsNone(await self.directory.find("p4"))
        self.assertIsNone(await self.directory.find("nope"))

    async def test_keeps_the_channels_for_twelve_hours(self):
        await self.directory.channels()
        self.clock.now += radio.CHANNELS_CACHE_SECONDS - 1
        await self.directory.find("p3")
        self.assertEqual(len(self.api.requests), 1)
        self.api.documents[radio.CHANNELS_URL] = document_with(
            "p3", audioAssets=[{"format": "HLS", "url": STREAM.format("moved")}]
        )
        self.clock.now += 1
        self.assertEqual((await self.directory.find("p3")).stream_url, STREAM.format("moved"))
        self.assertEqual(len(self.api.requests), 2)

    async def test_the_last_channels_when_dr_cannot_be_read(self):
        await self.directory.channels()
        self.clock.now += radio.CHANNELS_CACHE_SECONDS
        self.api.fail(radio.CHANNELS_URL)
        with self.assertLogs(LOGGER_NAME, "WARNING") as logs:
            channels = await self.directory.channels()
        self.assertEqual(slugs(channels), slugs(CHANNEL_LIST))
        self.assertIn("Could not load the document", logs.output[0])

    async def test_tries_again_after_ten_minutes(self):
        await self.directory.channels()
        self.clock.now += radio.CHANNELS_CACHE_SECONDS
        self.api.fail(radio.CHANNELS_URL)
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            await self.directory.channels()
            self.clock.now += api.RETRY_SECONDS - 1
            await self.directory.channels()
        self.assertEqual(len(self.api.requests), 2)
        self.api.fail()
        self.clock.now += 1
        await self.directory.channels()
        self.assertEqual(len(self.api.requests), 3)

    async def test_an_error_when_dr_cannot_be_read_and_nothing_was_read_before(self):
        self.api.fail(radio.CHANNELS_URL)
        with self.assertRaises(api.ApiError):
            await self.directory.channels()

    async def test_an_error_for_a_document_without_channels(self):
        self.api.documents[radio.CHANNELS_URL] = [{"slug": "p9"}]
        with self.assertRaisesRegex(api.ApiError, "no radio channels"):
            await self.directory.channels()
        self.api.documents[radio.CHANNELS_URL] = {"error": "maintenance"}
        with self.assertRaises(api.ApiError):
            await self.directory.find("p3")

    async def test_a_bad_document_keeps_the_last_channels(self):
        await self.directory.channels()
        self.clock.now += radio.CHANNELS_CACHE_SECONDS
        self.api.documents[radio.CHANNELS_URL] = []
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            self.assertEqual(slugs(await self.directory.channels()), slugs(CHANNEL_LIST))


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
        self.assertEqual(picture, api.IMAGE_URL.format("urn:dr:radio:image:b"))

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


class Documents(FakeApi):
    """Stands in for the now-playing documents of DR's radio API."""

    def __init__(self):
        super().__init__(
            {
                CHANNELS["p3"].tracks_url: TRACKS_P3,
                CHANNELS["p2"].tracks_url: TRACKS_P2,
                CHANNELS["p8jazz"].tracks_url: {"items": []},
                radio.PROGRAMMES_URL: PROGRAMMES,
            }
        )


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

    async def test_the_programme_when_dr_refuses_the_track_list(self):
        self.api.errors[CHANNELS["p3"].tracks_url] = api.NotFoundError("DR has nothing there")
        self.assertEqual(
            await self.resolve(), radio.NowPlaying("Den sidste time", None, P3_PICTURE)
        )

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
