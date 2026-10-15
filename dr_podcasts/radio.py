"""The DR live radio channels and how their stream urls are found."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

PAGE_URL = "https://www.dr.dk/lyd/{}"
FALLBACK_STREAM_URL = "https://drliveradio2.akamaized.net/hls/live/2118698/{}/masterab.m3u8"
LOGO_URL = "https://www.dr.dk/lyd/_next/static/media/{}.svg"
STREAM_CACHE_SECONDS = 12 * 3600
STREAM_RETRY_SECONDS = 10 * 60


@dataclass(frozen=True, slots=True)
class Channel:
    """One DR radio channel."""

    slug: str
    name: str
    stream_slug: str
    logo: str

    @property
    def page_url(self) -> str:
        """Return the url of the channel page, which holds the stream url."""
        return PAGE_URL.format(self.slug)

    @property
    def fallback_stream_url(self) -> str:
        """Return the stream url to use when the page does not give one."""
        return FALLBACK_STREAM_URL.format(self.stream_slug)

    @property
    def logo_url(self) -> str:
        """Return the url of the channel logo."""
        return LOGO_URL.format(self.logo)


CHANNELS = (
    Channel("p1", "DR P1", "p1", "p1.29c35f9c"),
    Channel("p2", "DR P2", "p2", "p2.041e766f"),
    Channel("p3", "DR P3", "p3", "p3.28743ad0"),
    Channel("p4kbh", "DR P4 København", "p4kobenhavn", "p4.a9a465ed"),
    Channel("p5kbh", "DR P5 København", "p5kobenhavn", "p5.1704bd78"),
    Channel("p6beat", "DR P6 Beat", "p6", "p6beat.4efb4634"),
    Channel("p8jazz", "DR P8 Jazz", "p8", "p8jazz.3748d702"),
)
CHANNELS_BY_SLUG = {channel.slug: channel for channel in CHANNELS}


class StreamPageError(Exception):
    """A channel page could not be loaded."""


def find_stream_url(html: str, channel: Channel) -> str | None:
    """
    Return the HLS stream url of the channel from a dr.dk/lyd page.

    Every page lists the streams of all the channels, so the channel's own stream is picked
    out by its stream slug. The host and the numeric id in the url change from time to time.
    """
    pattern = (
        r"https://drliveradio\d*\.akamaized\.net/hls/live/\d+/"
        rf"{re.escape(channel.stream_slug)}/masterab\.m3u8"
    )
    match = re.search(pattern, html)
    return match.group(0) if match else None


def search_channels(query: str, limit: int) -> list[Channel]:
    """Return the channels whose name holds every word of the query, ignoring case."""
    words = query.casefold().split()
    if not words:
        return []
    matches = [
        channel for channel in CHANNELS if all(word in channel.name.casefold() for word in words)
    ]
    return matches[:limit]


class StreamResolver:
    """
    Finds the stream url of a channel from its page and remembers the answer.

    The answer is kept for STREAM_CACHE_SECONDS. When the page cannot be read the last url
    that was found is used, and the channel's fallback url before any was found. Such a
    fallback is kept for STREAM_RETRY_SECONDS before the page is tried again.
    """

    def __init__(
        self,
        fetch_page: Callable[[str], Awaitable[str]],
        logger: Logger,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a coroutine that returns a page or raises StreamPageError."""
        self._fetch_page = fetch_page
        self._logger = logger
        self._clock = clock
        self._urls: dict[str, tuple[str, float]] = {}

    async def resolve(self, channel: Channel) -> str:
        """Return the stream url of the channel."""
        cached = self._urls.get(channel.slug)
        if cached and self._clock() < cached[1]:
            return cached[0]
        url: str | None = None
        reason = f"{channel.page_url} lists no stream for it"
        try:
            url = find_stream_url(await self._fetch_page(channel.page_url), channel)
        except StreamPageError as err:
            reason = str(err)
        if url is not None:
            self._urls[channel.slug] = (url, self._clock() + STREAM_CACHE_SECONDS)
            return url
        url = cached[0] if cached else channel.fallback_stream_url
        self._logger.warning(
            "Could not find the stream of %s, using %s: %s", channel.name, url, reason
        )
        self._urls[channel.slug] = (url, self._clock() + STREAM_RETRY_SECONDS)
        return url
