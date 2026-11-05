import copy
import logging
import unittest

from support import LOGGER_NAME, Clock, FakeApi, api, epoch, fixture, podcasts

SERIES_DOCUMENT = fixture("series.json")
EPISODES_DOCUMENT = fixture("episodes.json")
SERIES = {series.id: series for series in map(podcasts.parse_series, SERIES_DOCUMENT["items"])}
DEN_SIDSTE_TIME = "6641303469000"
GENSTART = "2642056922000"
SARA_OG_MONOPOLET = "1090358387000"
MUSIKQUIZZEN = "757024345000"
TAETTERE_PAA_HIMLEN = "2553137358000"
DE_LOVLOESE = "3337857881000"
RADIOAVISEN = "2467664383000"
BARNELIV = "5711523608000"
UMBRELLA_EPISODES = [
    "16122593411",
    "16122593412",
    "16122593413",
    "16122293321",
    "16122293322",
    "16122293323",
    "16122293324",
]
ASSET = "https://api.dr.dk/radio/v1/assetlinks/urn:dr:radio:episode:{}/{}"
CATALOGUE_URL = "https://api.dr.dk/radio/v5/series?limit=500"
CATALOGUE_NEXT_URL = "https://api.dr.dk/radio/v5/series?limit=500&offset={}"
SERIES_URL = "https://api.dr.dk/radio/v5/series/{}"
GROUPS_URL = "https://api.dr.dk/radio/v5/series/{}/episodes/groups?limit=500"
GROUP_URL = "https://api.dr.dk/radio/v5/series/{}/episodes/groups/{}?limit=500&offset={}"
EPISODE_URL = "https://api.dr.dk/radio/v5/episodes/{}"
NOT_AN_ID = [
    "https://example.com/feed.xml",
    "https://example.com/feed.xml https://example.com/guid/6427350",
    "abc",
    "",
    " 12",
    "12 34",
    "12/34",
    "../12",
    "-12",
]


def series_item(number):
    return next(i for i in SERIES_DOCUMENT["items"] if i["productionNumber"] == number)


def episode_items(series_number):
    return [item for group in EPISODES_DOCUMENT[series_number] for item in group["items"]]


def episode_item(number):
    return next(
        item
        for groups in EPISODES_DOCUMENT.values()
        for group in groups
        for item in group["items"]
        if item["productionNumber"] == number
    )


def asset(target, format_, bitrate, url):
    return {"target": target, "format": format_, "bitrate": bitrate, "url": url}


def make_series(title, description=None, **changes):
    fields = {
        "id": title,
        "title": title,
        "description": description,
        "episodes": 1,
        "latest": None,
        "image_url": None,
        "genres": (),
        "explicit": False,
        "page_url": None,
    }
    return podcasts.Series(**{**fields, **changes})


def titles(series):
    return [entry.title for entry in series]


class ParseSeriesTest(unittest.TestCase):
    def test_a_series(self):
        series = SERIES[DEN_SIDSTE_TIME]
        self.assertEqual(series.id, DEN_SIDSTE_TIME)
        self.assertEqual(series.title, "Den sidste time")
        self.assertTrue(series.description.startswith("Chano Jørgensen og Kathrine"))
        self.assertEqual(series.episodes, 2)
        self.assertEqual(series.latest, epoch("22:00:00", "2026-10-22"))
        self.assertEqual(series.genres, ("Underholdning",))
        self.assertFalse(series.explicit)
        self.assertEqual(series.page_url, "https://www.dr.dk/lyd/p3/den-sidste-time-6641303469000")

    def test_every_series_of_the_fixture(self):
        self.assertEqual(
            sorted(SERIES),
            sorted(
                [
                    DEN_SIDSTE_TIME,
                    GENSTART,
                    SARA_OG_MONOPOLET,
                    MUSIKQUIZZEN,
                    TAETTERE_PAA_HIMLEN,
                    DE_LOVLOESE,
                    RADIOAVISEN,
                    BARNELIV,
                ]
            ),
        )

    def test_the_picture_is_the_podcast_one_else_the_square_one(self):
        self.assertEqual(
            SERIES[DEN_SIDSTE_TIME].image_url,
            api.IMAGE_URL.format("urn:dr:radio:image:6ad9f2f072191fe5360b406b"),
        )
        self.assertEqual(
            SERIES[BARNELIV].image_url,
            api.IMAGE_URL.format("urn:dr:radio:image:68b840807964fa5412a0ecf6"),
        )
        item = dict(series_item(GENSTART), imageAssets=[])
        self.assertIsNone(podcasts.parse_series(item).image_url)

    def test_the_genres(self):
        self.assertEqual(SERIES[MUSIKQUIZZEN].genres, ("Musik", "Underholdning"))
        item = dict(series_item(MUSIKQUIZZEN), categories=["Musik", " ", "Musik", None, 5, "Quiz"])
        self.assertEqual(podcasts.parse_series(item).genres, ("Musik", "Quiz"))
        for categories in (None, "Musik", []):
            item = dict(series_item(MUSIKQUIZZEN), categories=categories)
            self.assertEqual(podcasts.parse_series(item).genres, ())

    def test_the_number_of_episodes(self):
        self.assertEqual(SERIES[GENSTART].episodes, 1537)
        for value in (None, "12", -3, 2.5):
            item = dict(series_item(GENSTART), numberOfEpisodes=value)
            self.assertEqual(podcasts.parse_series(item).episodes, 0, value)

    def test_the_time_of_the_latest_episode(self):
        self.assertEqual(SERIES[GENSTART].latest, epoch("02:00:00"))
        self.assertLess(SERIES[BARNELIV].latest, 0)
        for value in (None, "", "yesterday", "2026-11-05T02:00:00"):
            item = dict(series_item(GENSTART), latestEpisodeStartTime=value)
            self.assertIsNone(podcasts.parse_series(item).latest, value)

    def test_explicit_content(self):
        item = dict(series_item(GENSTART), explicitContent=True)
        self.assertTrue(podcasts.parse_series(item).explicit)
        item = dict(series_item(GENSTART), explicitContent="yes")
        self.assertFalse(podcasts.parse_series(item).explicit)

    def test_leaves_out_a_series_that_is_not_available(self):
        item = dict(series_item(GENSTART), isAvailableOnDemand=False)
        self.assertIsNone(podcasts.parse_series(item))
        del item["isAvailableOnDemand"]
        self.assertIsNotNone(podcasts.parse_series(item))

    def test_leaves_out_a_series_without_an_id_or_a_title(self):
        changes = [{"productionNumber": value} for value in (None, 2642056922000, True, *NOT_AN_ID)]
        changes += [{"title": None}, {"title": "  "}]
        for change in changes:
            self.assertIsNone(podcasts.parse_series(dict(series_item(GENSTART), **change)), change)

    def test_unreadable_items(self):
        for item in (None, 5, "x", [], {}):
            self.assertIsNone(podcasts.parse_series(item))


class ParseEpisodeTest(unittest.TestCase):
    def test_an_episode(self):
        episode = podcasts.parse_episode(episode_item("13332646601"))
        self.assertEqual(episode.id, "13332646601")
        self.assertEqual(episode.title, "Fredag 23. oktober 2026")
        self.assertTrue(episode.description.startswith("Chano Jørgensen og Kathrine"))
        self.assertEqual(episode.duration, 2104)
        self.assertEqual(episode.published, epoch("22:00:00", "2026-10-22"))
        self.assertEqual(
            episode.image_url, api.IMAGE_URL.format("urn:dr:radio:image:6ad9f2f072191fe5360b406b")
        )
        self.assertFalse(episode.explicit)
        self.assertTrue(episode.page_url.endswith("/fredag-23-oktober-2026-13332646601"))
        self.assertEqual(episode.series_id, DEN_SIDSTE_TIME)
        self.assertEqual(episode.series_title, "Den sidste time")

    def test_the_mp3_with_the_highest_bitrate_is_played(self):
        episode = podcasts.parse_episode(episode_item("13332646601"))
        self.assertEqual(
            episode.audio_url,
            ASSET.format(
                "6ad74f730a53387c03882d19",
                "347bb34d0e730032a186bf6590b2fda614a7a95e664d67b1f6a3573a91059063",
            ),
        )
        self.assertFalse(episode.hls)

    def test_the_mp3_is_found_wherever_the_asset_is_listed(self):
        item = copy.deepcopy(episode_item("16122293321"))
        expected = ASSET.format(
            "633ff9514c57f9f719d27463",
            "674ea56ead83b9491cd27e4cc0acc945f30f042284cd96999c28ef75b2ca9ea8",
        )
        for assets in (item["audioAssets"], item["audioAssets"][::-1]):
            episode = podcasts.parse_episode(dict(item, audioAssets=assets))
            self.assertEqual((episode.audio_url, episode.hls), (expected, False))

    def test_the_hls_stream_when_there_is_no_mp3(self):
        episode = podcasts.parse_episode(episode_item("13332646424"))
        self.assertTrue(episode.hls)
        self.assertEqual(
            episode.audio_url,
            ASSET.format(
                "6ac618f3842017fe110f01f4",
                "20cc9a1c4b7f4100884bae9aa2ec925d3b6ca09dec292d9a85f32681dafc5afc",
            ),
        )
        self.assertEqual(episode.duration, 7200)

    def test_the_mp3_is_preferred_to_the_hls_stream(self):
        item = dict(
            episode_item("13332646424"),
            audioAssets=[
                asset("Stream", "HLS", 9999, "https://example.com/stream.m3u8"),
                asset("Progressive", "mp3", 0, "https://example.com/a.mp3"),
                asset("Progressive", "mp3", None, "https://example.com/b.mp3"),
            ],
        )
        episode = podcasts.parse_episode(item)
        self.assertEqual((episode.audio_url, episode.hls), ("https://example.com/a.mp3", False))

    def test_leaves_out_an_episode_without_audio_to_play(self):
        for assets in (
            None,
            [],
            "text",
            [None, 5, {}],
            [asset("Progressive", "mp4", 192, "https://example.com/a.mp4")],
            [asset("Progressive", "mp3", 192, " ")],
            [asset("Progressive", "mp3", 192, "ftp://example.com/a.mp3")],
            [asset("Progressive", "mp3", 192, None)],
        ):
            item = dict(episode_item("13332646601"), audioAssets=assets)
            self.assertIsNone(podcasts.parse_episode(item), assets)

    def test_the_time_of_an_episode_is_when_it_started_else_when_it_was_published(self):
        item = dict(episode_item("13332646601"), startTime=None)
        self.assertEqual(podcasts.parse_episode(item).published, epoch("22:00:00", "2026-10-22"))
        item = dict(item, publishTime="2026-10-24T10:00:00+00:00")
        self.assertEqual(podcasts.parse_episode(item).published, epoch("10:00:00", "2026-10-24"))
        item = dict(item, publishTime="soon")
        self.assertIsNone(podcasts.parse_episode(item).published)

    def test_the_duration_in_seconds(self):
        for value, seconds in ((1500, 1), (60000, 60), (None, 0), (0, 0), (-5, 0), ("60000", 0)):
            item = dict(episode_item("13332646601"), durationMilliseconds=value)
            self.assertEqual(podcasts.parse_episode(item).duration, seconds, value)

    def test_an_episode_of_an_umbrella_names_the_series_it_belongs_to(self):
        episode = podcasts.parse_episode(episode_item("16122593411"))
        self.assertEqual(episode.title, "1:3 - Odderhusets hemmelighed: Familien i skoven")
        self.assertEqual(episode.series_id, "6640504587581")
        self.assertEqual(episode.series_title, "Odderhusets hemmelighed")

    def test_an_episode_without_a_series_has_no_series(self):
        for series in (None, "x", {}, {"productionNumber": "x"}):
            item = dict(episode_item("13332646601"), series=series)
            episode = podcasts.parse_episode(item)
            self.assertEqual((episode.series_id, episode.series_title), (None, None))

    def test_leaves_out_an_episode_that_is_not_available(self):
        item = dict(episode_item("13332646601"), isAvailableOnDemand=False)
        self.assertIsNone(podcasts.parse_episode(item))

    def test_leaves_out_an_episode_without_an_id_or_a_title(self):
        changes = [{"productionNumber": value} for value in (None, 13332646601, *NOT_AN_ID)]
        changes += [{"title": None}, {"title": " "}]
        for change in changes:
            item = dict(episode_item("13332646601"), **change)
            self.assertIsNone(podcasts.parse_episode(item), change)

    def test_unreadable_items(self):
        for item in (None, 5, "x", [], {}):
            self.assertIsNone(podcasts.parse_episode(item))


class SearchSeriesTest(unittest.TestCase):
    def test_finds_the_series_by_its_title(self):
        found = podcasts.search_series(SERIES.values(), "den sidste time", 5)
        self.assertEqual(titles(found), ["Den sidste time"])

    def test_ignores_case_and_extra_spaces(self):
        found = podcasts.search_series(SERIES.values(), "  DEN   sidste TIME ", 5)
        self.assertEqual(titles(found), ["Den sidste time"])

    def test_every_word_must_match(self):
        found = podcasts.search_series(SERIES.values(), "sidste time", 5)
        self.assertEqual(titles(found), ["Den sidste time"])
        self.assertEqual(podcasts.search_series(SERIES.values(), "sidste zebra", 5), [])

    def test_the_description_is_searched_too(self):
        found = podcasts.search_series(SERIES.values(), "nyhedspodcast", 5)
        self.assertEqual(titles(found), ["Genstart"])

    def test_the_words_may_be_split_between_the_title_and_the_description(self):
        everything = [make_series("Genstart", "Dagens nyheder")]
        found = podcasts.search_series(everything, "genstart nyheder", 5)
        self.assertEqual(titles(found), ["Genstart"])

    def test_a_title_that_starts_with_the_query_comes_first_then_other_titles(self):
        everything = [
            make_series("Det store hus", "tid"),
            make_series("Tidens tegn"),
            make_series("Kære tid"),
            make_series("Om en tid", "x"),
            make_series("Tid til alt"),
            make_series("Alt andet", "Her handler det om tid"),
        ]
        self.assertEqual(
            titles(podcasts.search_series(everything, "tid", 10)),
            ["Tid til alt", "Tidens tegn", "Kære tid", "Om en tid", "Alt andet", "Det store hus"],
        )

    def test_limit(self):
        everything = [make_series(f"Tid {number}") for number in range(10)]
        self.assertEqual(len(podcasts.search_series(everything, "tid", 3)), 3)

    def test_a_blank_query_matches_nothing(self):
        self.assertEqual(podcasts.search_series(SERIES.values(), "   ", 5), [])


class RecentlyUpdatedTest(unittest.TestCase):
    def test_the_newest_first(self):
        self.assertEqual(
            titles(podcasts.recently_updated(SERIES.values(), 20)),
            [
                "Radioavisen",
                "Genstart",
                "Musikquizzen",
                "Sara & Monopolet - podcast",
                "Tættere på himlen",
                "Den sidste time",
                "De Lovløse",
                "Barneliv gennem tiden",
            ],
        )

    def test_limit(self):
        self.assertEqual(len(podcasts.recently_updated(SERIES.values(), 3)), 3)

    def test_leaves_out_series_without_episodes_or_a_time(self):
        everything = [
            make_series("Old", latest=100.0),
            make_series("Empty", episodes=0, latest=900.0),
            make_series("Undated", latest=None),
            make_series("New", latest=500.0),
        ]
        self.assertEqual(titles(podcasts.recently_updated(everything, 10)), ["New", "Old"])


def catalogue_documents(pages):
    """Return the documents of a catalogue split in pages, served by the urls that link them."""
    documents = {}
    offset = 0
    for index, items in enumerate(pages):
        page = {"type": "List", "totalSize": sum(map(len, pages)), "items": items}
        if index < len(pages) - 1:
            page["next"] = CATALOGUE_NEXT_URL.format(offset + len(items))
        documents[CATALOGUE_URL if offset == 0 else CATALOGUE_NEXT_URL.format(offset)] = page
        offset += len(items)
    return documents


class DirectoryTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.api = FakeApi()
        self.clock = Clock()
        self.directory = podcasts.PodcastDirectory(
            self.api, logging.getLogger(LOGGER_NAME), self.clock
        )

    def ids(self, episodes):
        return [episode.id for episode in episodes]


class CatalogueTest(DirectoryTest):
    def setUp(self):
        super().setUp()
        items = SERIES_DOCUMENT["items"]
        self.api.documents = catalogue_documents([items[:5], items[5:]])

    async def test_reads_every_page_of_the_series(self):
        catalogue = await self.directory.catalogue()
        self.assertEqual(sorted(entry.id for entry in catalogue), sorted(SERIES))
        self.assertEqual(self.api.requests, [CATALOGUE_URL, CATALOGUE_NEXT_URL.format(5)])

    async def test_follows_the_links_between_the_pages(self):
        items = SERIES_DOCUMENT["items"]
        self.api.documents = catalogue_documents([items[:3], items[3:4], items[4:]])
        self.assertEqual(len(await self.directory.catalogue()), 8)
        self.assertEqual(
            self.api.requests,
            [CATALOGUE_URL, CATALOGUE_NEXT_URL.format(3), CATALOGUE_NEXT_URL.format(4)],
        )

    async def test_one_request_when_everything_is_on_the_first_page(self):
        self.api.documents = catalogue_documents([SERIES_DOCUMENT["items"]])
        self.assertEqual(len(await self.directory.catalogue()), 8)
        self.assertEqual(self.api.requests, [CATALOGUE_URL])

    async def test_stops_at_a_page_without_series(self):
        self.api.documents = {
            CATALOGUE_URL: {
                "items": SERIES_DOCUMENT["items"][:2],
                "next": CATALOGUE_NEXT_URL.format(2),
            },
            CATALOGUE_NEXT_URL.format(2): {"items": [], "next": CATALOGUE_NEXT_URL.format(2)},
        }
        self.assertEqual(len(await self.directory.catalogue()), 2)
        self.assertEqual(len(self.api.requests), 2)

    async def test_stops_after_a_hundred_pages(self):
        page = {"items": SERIES_DOCUMENT["items"][:1], "next": CATALOGUE_NEXT_URL.format(1)}
        self.api.documents = {CATALOGUE_URL: page, CATALOGUE_NEXT_URL.format(1): page}
        self.assertEqual(len(await self.directory.catalogue()), 1)
        self.assertEqual(len(self.api.requests), podcasts.MAX_PAGES)

    async def test_does_not_follow_a_page_that_is_not_on_the_api_of_dr(self):
        page = {"items": SERIES_DOCUMENT["items"], "next": "https://example.com/series?offset=8"}
        self.api.documents = {CATALOGUE_URL: page}
        with self.assertRaisesRegex(api.ApiError, "not on its API"):
            await self.directory.catalogue()
        self.assertEqual(self.api.requests, [CATALOGUE_URL])

    async def test_leaves_out_series_that_are_not_available_and_duplicates(self):
        items = copy.deepcopy(SERIES_DOCUMENT["items"][:3])
        items[1]["isAvailableOnDemand"] = False
        items.append(dict(items[0], title="A copy"))
        self.api.documents = catalogue_documents([items])
        catalogue = await self.directory.catalogue()
        self.assertEqual(titles(catalogue), ["Den sidste time", "Sara & Monopolet - podcast"])

    async def test_keeps_the_catalogue_for_an_hour(self):
        await self.directory.catalogue()
        self.clock.now += podcasts.CATALOGUE_CACHE_SECONDS - 1
        await self.directory.catalogue()
        self.assertEqual(len(self.api.requests), 2)
        self.clock.now += 1
        await self.directory.catalogue()
        self.assertEqual(len(self.api.requests), 4)

    async def test_the_last_catalogue_when_dr_cannot_be_read(self):
        await self.directory.catalogue()
        self.clock.now += podcasts.CATALOGUE_CACHE_SECONDS
        self.api.fail(CATALOGUE_NEXT_URL.format(5))
        with self.assertLogs(LOGGER_NAME, "WARNING") as logs:
            catalogue = await self.directory.catalogue()
        self.assertEqual(len(catalogue), 8)
        self.assertIn("the DR podcast list", logs.output[0])

    async def test_an_error_when_dr_cannot_be_read_and_nothing_was_read_before(self):
        self.api.fail(CATALOGUE_URL)
        with self.assertRaises(api.ApiError):
            await self.directory.catalogue()

    async def test_an_error_for_a_catalogue_without_series(self):
        for document in ({"items": []}, {"message": "maintenance"}, [], None):
            self.api.documents = {CATALOGUE_URL: document}
            with self.assertRaisesRegex(api.ApiError, "no podcasts", msg=document):
                await self.directory.catalogue()


class SeriesTest(DirectoryTest):
    def setUp(self):
        super().setUp()
        self.api.documents = {
            SERIES_URL.format(item["productionNumber"]): item for item in SERIES_DOCUMENT["items"]
        }

    async def test_reads_a_series_by_its_production_number(self):
        series = await self.directory.series(DEN_SIDSTE_TIME)
        self.assertEqual(series, SERIES[DEN_SIDSTE_TIME])
        self.assertEqual(self.api.requests, ["https://api.dr.dk/radio/v5/series/6641303469000"])

    async def test_keeps_a_series_for_a_quarter_of_an_hour(self):
        await self.directory.series(GENSTART)
        self.clock.now += podcasts.PODCAST_CACHE_SECONDS - 1
        await self.directory.series(GENSTART)
        self.assertEqual(len(self.api.requests), 1)
        self.clock.now += 1
        await self.directory.series(GENSTART)
        self.assertEqual(len(self.api.requests), 2)

    async def test_series_are_kept_apart(self):
        await self.directory.series(GENSTART)
        await self.directory.series(MUSIKQUIZZEN)
        await self.directory.series(GENSTART)
        self.assertEqual(len(self.api.requests), 2)

    async def test_not_found_when_dr_has_no_such_series(self):
        with self.assertRaises(api.NotFoundError):
            await self.directory.series("999999999999")

    async def test_not_found_without_a_request_for_what_cannot_be_an_id(self):
        for series_id in NOT_AN_ID:
            with self.assertRaises(api.NotFoundError, msg=series_id):
                await self.directory.series(series_id)
            with self.assertRaises(api.NotFoundError, msg=series_id):
                await self.directory.episodes(series_id)
        self.assertEqual(self.api.requests, [])

    async def test_not_found_when_the_series_is_not_available(self):
        document = dict(series_item(GENSTART), isAvailableOnDemand=False)
        self.api.documents[SERIES_URL.format(GENSTART)] = document
        with self.assertRaises(api.NotFoundError):
            await self.directory.series(GENSTART)

    async def test_the_last_copy_when_dr_cannot_be_read(self):
        await self.directory.series(GENSTART)
        self.clock.now += podcasts.PODCAST_CACHE_SECONDS
        self.api.fail(SERIES_URL.format(GENSTART))
        with self.assertLogs(LOGGER_NAME, "WARNING") as logs:
            self.assertEqual(await self.directory.series(GENSTART), SERIES[GENSTART])
        self.assertIn(GENSTART, logs.output[0])

    async def test_a_series_that_dr_has_dropped_is_not_served_again(self):
        await self.directory.series(GENSTART)
        self.clock.now += podcasts.PODCAST_CACHE_SECONDS
        del self.api.documents[SERIES_URL.format(GENSTART)]
        for _ in range(2):
            with self.assertRaises(api.NotFoundError):
                await self.directory.series(GENSTART)


class EpisodesTest(DirectoryTest):
    def setUp(self):
        super().setUp()
        self.api.documents = {
            GROUPS_URL.format(number): groups for number, groups in EPISODES_DOCUMENT.items()
        }
        self.api.documents.update(
            {
                EPISODE_URL.format(item["productionNumber"]): item
                for number in EPISODES_DOCUMENT
                for item in episode_items(number)
            }
        )

    async def test_reads_the_episodes_of_a_series(self):
        episodes = await self.directory.episodes(DEN_SIDSTE_TIME)
        self.assertEqual(self.ids(episodes), ["13332646601", "13332646424"])
        self.assertEqual([episode.hls for episode in episodes], [False, True])
        self.assertEqual(self.api.requests, [GROUPS_URL.format(DEN_SIDSTE_TIME)])

    async def test_reads_every_group_of_a_series_made_of_other_series(self):
        episodes = await self.directory.episodes(DE_LOVLOESE)
        self.assertEqual(self.ids(episodes), UMBRELLA_EPISODES)
        self.assertEqual(
            {episode.series_title for episode in episodes},
            {"Odderhusets hemmelighed", "Harlems Robin Hood"},
        )

    async def test_follows_the_links_between_the_pages_of_a_group(self):
        items = episode_items(DE_LOVLOESE)
        first, second = (
            GROUP_URL.format(DE_LOVLOESE, "all", 3),
            GROUP_URL.format(DE_LOVLOESE, "all", 5),
        )
        self.api.documents[GROUPS_URL.format(DE_LOVLOESE)] = [
            {"groupId": "all", "items": items[:3], "next": first}
        ]
        self.api.documents[first] = {"groupId": "all", "items": items[3:5], "next": second}
        self.api.documents[second] = {"groupId": "all", "items": items[5:]}
        episodes = await self.directory.episodes(DE_LOVLOESE)
        self.assertEqual(self.ids(episodes), UMBRELLA_EPISODES)
        self.assertEqual(self.api.requests, [GROUPS_URL.format(DE_LOVLOESE), first, second])

    async def test_does_not_follow_a_page_that_is_not_on_the_api_of_dr(self):
        group = dict(EPISODES_DOCUMENT[DEN_SIDSTE_TIME][0], next="https://example.com/more")
        self.api.documents[GROUPS_URL.format(DEN_SIDSTE_TIME)] = [group]
        with self.assertRaisesRegex(api.ApiError, "not on its API"):
            await self.directory.episodes(DEN_SIDSTE_TIME)
        self.assertEqual(self.api.requests, [GROUPS_URL.format(DEN_SIDSTE_TIME)])

    async def test_stops_after_a_hundred_pages_of_a_group(self):
        url = GROUP_URL.format(DEN_SIDSTE_TIME, "all", 2)
        group = dict(EPISODES_DOCUMENT[DEN_SIDSTE_TIME][0], next=url)
        self.api.documents[GROUPS_URL.format(DEN_SIDSTE_TIME)] = [group]
        self.api.documents[url] = group
        self.assertEqual(len(await self.directory.episodes(DEN_SIDSTE_TIME)), 2)
        self.assertEqual(len(self.api.requests), podcasts.MAX_PAGES)

    async def test_leaves_out_episodes_that_cannot_be_played_and_duplicates(self):
        groups = copy.deepcopy(EPISODES_DOCUMENT[DE_LOVLOESE])
        groups[0]["items"][0]["isAvailableOnDemand"] = False
        groups[0]["items"][1]["audioAssets"] = []
        groups[1]["items"].append(copy.deepcopy(groups[0]["items"][2]))
        self.api.documents[GROUPS_URL.format(DE_LOVLOESE)] = groups
        episodes = await self.directory.episodes(DE_LOVLOESE)
        self.assertEqual(self.ids(episodes), ["16122593413", *UMBRELLA_EPISODES[3:]])

    async def test_no_episodes(self):
        self.api.documents[GROUPS_URL.format(DEN_SIDSTE_TIME)] = []
        self.assertEqual(await self.directory.episodes(DEN_SIDSTE_TIME), [])
        groups = [{"groupId": "x", "items": []}, {"items": None}, None, "text"]
        self.api.documents[GROUPS_URL.format(GENSTART)] = groups
        self.assertEqual(await self.directory.episodes(GENSTART), [])

    async def test_an_error_for_a_document_that_is_not_a_list_of_groups(self):
        for document in ({"items": []}, "text", None, 5):
            self.api.documents[GROUPS_URL.format(DEN_SIDSTE_TIME)] = document
            with self.assertRaises(api.ApiError, msg=document):
                await self.directory.episodes(DEN_SIDSTE_TIME)

    async def test_not_found_when_dr_has_no_such_series(self):
        with self.assertRaises(api.NotFoundError):
            await self.directory.episodes("999999999999")

    async def test_keeps_the_episodes_for_a_quarter_of_an_hour(self):
        await self.directory.episodes(DEN_SIDSTE_TIME)
        self.clock.now += podcasts.PODCAST_CACHE_SECONDS - 1
        await self.directory.episodes(DEN_SIDSTE_TIME)
        self.assertEqual(len(self.api.requests), 1)
        self.clock.now += 1
        await self.directory.episodes(DEN_SIDSTE_TIME)
        self.assertEqual(len(self.api.requests), 2)

    async def test_the_last_episodes_when_dr_cannot_be_read(self):
        await self.directory.episodes(DEN_SIDSTE_TIME)
        self.clock.now += podcasts.PODCAST_CACHE_SECONDS
        self.api.fail(GROUPS_URL.format(DEN_SIDSTE_TIME))
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            episodes = await self.directory.episodes(DEN_SIDSTE_TIME)
        self.assertEqual(self.ids(episodes), ["13332646601", "13332646424"])

    async def test_one_episode_by_its_production_number(self):
        episode = await self.directory.episode("13332646601")
        self.assertEqual(episode, podcasts.parse_episode(episode_item("13332646601")))
        self.assertEqual(self.api.requests, ["https://api.dr.dk/radio/v5/episodes/13332646601"])

    async def test_an_episode_is_read_again_every_time(self):
        await self.directory.episode("13332646601")
        await self.directory.episode("13332646601")
        self.assertEqual(len(self.api.requests), 2)

    async def test_an_episode_is_not_found_without_audio(self):
        item = dict(episode_item("13332646601"), audioAssets=[])
        self.api.documents[EPISODE_URL.format("13332646601")] = item
        with self.assertRaisesRegex(api.NotFoundError, "no audio"):
            await self.directory.episode("13332646601")

    async def test_an_episode_is_not_found_when_dr_has_none_with_the_id(self):
        with self.assertRaises(api.NotFoundError):
            await self.directory.episode("999999999999")

    async def test_an_episode_is_not_found_without_a_request_for_what_cannot_be_an_id(self):
        for episode_id in NOT_AN_ID:
            with self.assertRaises(api.NotFoundError, msg=episode_id):
                await self.directory.episode(episode_id)
        self.assertEqual(self.api.requests, [])

    async def test_an_error_when_dr_cannot_be_read(self):
        self.api.fail(EPISODE_URL.format("13332646601"))
        with self.assertRaises(api.ApiError):
            await self.directory.episode("13332646601")


if __name__ == "__main__":
    unittest.main()
