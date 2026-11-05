import asyncio
import logging
import unittest

from support import LOGGER_NAME, Clock, api


def square(image_id, target="Default", ratio="1:1"):
    return {"id": image_id, "target": target, "ratio": ratio}


class CleanTextTest(unittest.TestCase):
    def test_strips_text(self):
        self.assertEqual(api.clean_text("  P3 \n"), "P3")

    def test_none_for_what_is_not_text_with_something_in_it(self):
        for value in (None, "", "  ", 5, ["x"], {}):
            self.assertIsNone(api.clean_text(value))


class ParseTimeTest(unittest.TestCase):
    def test_a_time_with_an_offset(self):
        self.assertEqual(api.parse_time("2026-11-05T22:03:09+00:00"), 1793916189.0)
        self.assertEqual(api.parse_time("2026-11-05T23:03:09+01:00"), 1793916189.0)

    def test_none_without_an_offset_or_a_time(self):
        for value in ("2026-11-05T22:03:09", "a while ago", "", None, 5):
            self.assertIsNone(api.parse_time(value))

    def test_a_time_before_1970(self):
        self.assertLess(api.parse_time("1952-10-23T19:55:00+00:00"), 0)


class ImageUrlTest(unittest.TestCase):
    def test_a_square_picture(self):
        url = api.image_url([square("urn:dr:radio:image:a")])
        self.assertEqual(url, api.IMAGE_URL.format("urn:dr:radio:image:a"))
        self.assertTrue(url.startswith("https://asset.dr.dk/drlyd/images/urn:dr:radio:image:a?"))

    def test_other_shapes_are_not_used(self):
        self.assertIsNone(api.image_url([square("a", ratio="16:9"), square("b", ratio="4:3")]))

    def test_the_target_named_first_wins(self):
        assets = [square("a", "Default"), square("b", "SquareImage"), square("c", "Podcast")]
        self.assertEqual(api.image_url(assets, "Podcast", "SquareImage"), api.IMAGE_URL.format("c"))
        self.assertEqual(api.image_url(assets, "SquareImage"), api.IMAGE_URL.format("b"))

    def test_a_square_picture_of_another_target_beats_none(self):
        assets = [square("b", "Default"), square("a", "Other")]
        self.assertEqual(api.image_url(assets, "Podcast"), api.IMAGE_URL.format("a"))

    def test_none_for_unreadable_assets(self):
        for assets in (None, [], "text", {}, [None, 5, "x", {}, {"ratio": "1:1"}, square(" ")]):
            self.assertIsNone(api.image_url(assets))


class HeadersTest(unittest.TestCase):
    def test_the_requests_ask_for_json_with_a_key(self):
        self.assertEqual(api.API_HEADERS["Accept"], "application/json")
        self.assertRegex(api.API_HEADERS["x-apikey"], r"^[A-Za-z0-9]{20,}$")


class CacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.clock = Clock()
        self.loads = []
        self.fail = None
        self.cache = api.Cache(
            "the things", self.load, 100, logging.getLogger(LOGGER_NAME), self.clock
        )

    async def load(self, key):
        self.loads.append(key)
        await asyncio.sleep(0)
        if self.fail:
            raise self.fail
        return f"{key}@{self.clock.now}"

    async def test_loads_a_value_and_keeps_it_for_its_lifetime(self):
        self.assertEqual(await self.cache.get("a"), "a@1000.0")
        self.clock.now += 99
        self.assertEqual(await self.cache.get("a"), "a@1000.0")
        self.assertEqual(self.loads, ["a"])
        self.clock.now += 1
        self.assertEqual(await self.cache.get("a"), "a@1100.0")
        self.assertEqual(self.loads, ["a", "a"])

    async def test_keys_are_kept_apart(self):
        await self.cache.get("a")
        await self.cache.get("b")
        await self.cache.get("a")
        self.assertEqual(self.loads, ["a", "b"])

    async def test_one_load_for_callers_asking_together(self):
        values = await asyncio.gather(*(self.cache.get("a") for _ in range(5)))
        self.assertEqual(values, ["a@1000.0"] * 5)
        self.assertEqual(self.loads, ["a"])

    async def test_the_error_when_there_is_no_copy(self):
        self.fail = api.ApiError("Could not load the thing")
        with self.assertRaises(api.ApiError):
            await self.cache.get("a")
        self.fail = None
        self.assertEqual(await self.cache.get("a"), "a@1000.0")

    async def test_the_last_copy_when_it_cannot_be_loaded_again(self):
        await self.cache.get("a")
        self.clock.now += 100
        self.fail = api.ApiError("Could not load the thing")
        with self.assertLogs(LOGGER_NAME, "WARNING") as logs:
            self.assertEqual(await self.cache.get("a"), "a@1000.0")
        self.assertIn("the things a", logs.output[0])
        self.assertIn("Could not load the thing", logs.output[0])

    async def test_a_value_that_cannot_be_loaded_is_tried_again_after_ten_minutes(self):
        await self.cache.get("a")
        self.clock.now += 100
        self.fail = api.ApiError("Could not load the thing")
        with self.assertLogs(LOGGER_NAME, "WARNING"):
            await self.cache.get("a")
            self.clock.now += api.RETRY_SECONDS - 1
            await self.cache.get("a")
        self.assertEqual(self.loads, ["a", "a"])
        self.fail = None
        self.clock.now += 1
        self.assertEqual(await self.cache.get("a"), f"a@{self.clock.now}")
        self.assertEqual(self.loads, ["a", "a", "a"])

    async def test_a_value_that_dr_no_longer_has_is_dropped(self):
        await self.cache.get("a")
        self.clock.now += 100
        self.fail = api.NotFoundError("DR has nothing there")
        with self.assertRaises(api.NotFoundError):
            await self.cache.get("a")
        with self.assertRaises(api.NotFoundError):
            await self.cache.get("a")
        self.assertEqual(self.loads, ["a", "a", "a"])


if __name__ == "__main__":
    unittest.main()
