"""
The DR live radio channels, how their stream urls are found and what they are playing.

What a channel is playing comes from the radio API that the dr.dk/lyd web app itself calls,
https://api.dr.dk/radio/v5, with no login. Two documents are read, as JSON:

- GET /indexpoints/live/urn:dr:radio:channel:<id> lists the tracks the channel has played,
  newest first. The id is the last part of channel.id in the schedules document below, so every
  district has a list of its own. Per item the fields used are type ("Track"), title,
  playedTime (ISO 8601, the start of the track on the clock of the HLS stream),
  durationMilliseconds (what the track is scheduled to last), classical, description (the
  artist line: "Artist" or "Artist feat. Guest") and roles (role, name).
- GET /schedules/all/now lists the programme on air on every channel. Per item the fields used
  are channel.slug, title, endTime and imageAssets (id, target, ratio).

The tracks list is served without a key only while DR's CDN holds a copy that someone else
requested a few seconds earlier, so a busy channel mostly answers and a quiet one rarely does;
otherwise it answers 401. API_HEADERS carries the public x-apikey that the web app sends with
these requests.

DR lists a track well after it has started, and the stream is played from a few segments behind
its live edge, so what a listener hears is looked up STREAM_DELAY_SECONDS back in time. While a
track is not listed yet, the programme is shown.
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from logging import Logger

PAGE_URL = "https://www.dr.dk/lyd/{}"
FALLBACK_STREAM_URL = "https://drliveradio2.akamaized.net/hls/live/2118698/{}/masterab.m3u8"
LOGO_URL = "https://www.dr.dk/lyd/_next/static/media/{}.svg"
API_URL = "https://api.dr.dk/radio/v5"
TRACKS_URL = f"{API_URL}/indexpoints/live/urn:dr:radio:channel:{{}}"
PROGRAMMES_URL = f"{API_URL}/schedules/all/now"
IMAGE_URL = (
    "https://asset.dr.dk/drlyd/images/{}"
    "?im=AspectCrop%3D%28512%2C512%29%2CxPosition%3D.5%2CyPosition%3D.5%3BResize%3D%28512%2C512%29"
)
API_HEADERS = {"Accept": "application/json", "x-apikey": "6Wkh8s98Afx1ZAaTT4FuWODTmvWGDPpR"}
STREAM_CACHE_SECONDS = 12 * 3600
STREAM_RETRY_SECONDS = 10 * 60
NOW_PLAYING_CACHE_SECONDS = 30
STREAM_DELAY_SECONDS = 30
STREAM_METADATA_UPDATE_INTERVAL = 10


@dataclass(frozen=True, slots=True)
class Channel:
    """One DR radio channel."""

    slug: str
    name: str
    stream_slug: str
    logo: str
    channel_id: str

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

    @property
    def tracks_url(self) -> str:
        """Return the url of the list of tracks the channel has played."""
        return TRACKS_URL.format(self.channel_id)


CHANNELS = (
    Channel("p1", "DR P1", "p1", "p1.29c35f9c", "5fa156d1da351264f87b462d"),
    Channel("p2", "DR P2", "p2", "p2.041e766f", "5fa156d2da351264f87b4633"),
    Channel("p3", "DR P3", "p3", "p3.28743ad0", "5fa156d2da351264f87b4634"),
    Channel("p4kbh", "DR P4 København", "p4kobenhavn", "p4.a9a465ed", "5fa156d3da351264f87b4651"),
    Channel("p5kbh", "DR P5 København", "p5kobenhavn", "p5.1704bd78", "5fa156d5da351264f87b466f"),
    Channel("p6beat", "DR P6 Beat", "p6", "p6beat.4efb4634", "5fa156d2da351264f87b463e"),
    Channel("p8jazz", "DR P8 Jazz", "p8", "p8jazz.3748d702", "5fa156d1da351264f87b4631"),
)
CHANNELS_BY_SLUG = {channel.slug: channel for channel in CHANNELS}


class StreamPageError(Exception):
    """A channel page could not be loaded."""


class NowPlayingError(Exception):
    """A document about what a channel is playing could not be loaded."""


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


@dataclass(frozen=True, slots=True)
class Track:
    """One track that DR has played on a channel."""

    title: str
    artist: str | None
    start: float
    end: float


@dataclass(frozen=True, slots=True)
class Programme:
    """The programme on air on a channel."""

    title: str
    image_url: str | None
    end: float


@dataclass(frozen=True, slots=True)
class NowPlaying:
    """What a channel is playing: a track or a programme, with the picture of the programme."""

    title: str
    artist: str | None = None
    image_url: str | None = None


def _text(value: Any) -> str | None:
    """Return the value stripped, if it is text with something in it."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _parse_time(value: Any) -> float | None:
    """Return the epoch seconds of an ISO 8601 time that carries a UTC offset, else None."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment.timestamp() if moment.tzinfo else None


def _composers(item: dict[str, Any]) -> list[str]:
    """Return the names of the composers among the roles of a track, each one once."""
    roles = item.get("roles")
    names = [
        _text(role.get("name"))
        for role in (roles if isinstance(roles, list) else [])
        if isinstance(role, dict) and str(role.get("role")).casefold() == "komponist"
    ]
    return list(dict.fromkeys(name for name in names if name))


def _artist(item: dict[str, Any]) -> str | None:
    """
    Return the artist line of a track.

    It is DR's own line, except for classical music, where that is a long list of credits and
    the composers are named instead.
    """
    if item.get("classical") and (composers := _composers(item)):
        return ", ".join(composers)
    return _text(item.get("description"))


def parse_tracks(document: Any) -> list[Track]:
    """
    Return the tracks of a live index points document.

    Items that are not tracks, or that lack a title, a start or a duration, are left out.
    """
    items = document.get("items") if isinstance(document, dict) else None
    tracks = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get("type") != "Track":
            continue
        title = _text(item.get("title"))
        start = _parse_time(item.get("playedTime"))
        duration = item.get("durationMilliseconds")
        if title is None or start is None or not isinstance(duration, int | float) or duration <= 0:
            continue
        tracks.append(Track(title, _artist(item), start, start + duration / 1000))
    return tracks


def find_track(tracks: Iterable[Track], at: float) -> Track | None:
    """Return the track playing at the time, which is the latest one to have started."""
    playing = [track for track in tracks if track.start <= at < track.end]
    return max(playing, key=lambda track: track.start, default=None)


def _square_image_id(assets: Any) -> str | None:
    """Return the id of the square image among the image assets of a programme, if any."""
    candidates = []
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or asset.get("ratio") != "1:1":
            continue
        if image_id := _text(asset.get("id")):
            candidates.append((asset.get("target") != "SquareImage", image_id))
    return min(candidates)[1] if candidates else None


def find_programme(document: Any, channel: Channel, at: float) -> Programme | None:
    """Return the programme that the schedules document has on air on the channel at the time."""
    entries = document if isinstance(document, list) else []
    for entry in entries:
        info = entry.get("channel") if isinstance(entry, dict) else None
        if not isinstance(info, dict) or info.get("slug") != channel.slug:
            continue
        title = _text(entry.get("title"))
        end = _parse_time(entry.get("endTime"))
        if title is None or end is None or end <= at:
            return None
        image_id = _square_image_id(entry.get("imageAssets"))
        return Programme(title, IMAGE_URL.format(image_id) if image_id else None, end)
    return None


class NowPlayingResolver:
    """
    Finds what a channel is playing from DR's radio API and remembers the answers.

    Each document is fetched at most once per NOW_PLAYING_CACHE_SECONDS, whoever asks. A
    document that cannot be fetched is replaced by the last one that could be, which describes
    nothing once its times have passed. The channel's own name is the last resort.
    """

    def __init__(
        self,
        fetch_json: Callable[[str], Awaitable[Any]],
        logger: Logger,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        """Initialize with a coroutine that returns the JSON at a url or raises NowPlayingError."""
        self._fetch_json = fetch_json
        self._logger = logger
        self._clock = clock
        self._wall_clock = wall_clock
        self._documents: dict[str, tuple[Any, float]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def resolve(self, channel: Channel) -> NowPlaying:
        """Return the track the channel is playing, else its programme, else the channel."""
        tracks, programmes = await asyncio.gather(
            self._load(channel.tracks_url), self._load(PROGRAMMES_URL)
        )
        at = self._wall_clock() - STREAM_DELAY_SECONDS
        programme = find_programme(programmes, channel, at)
        image_url = programme.image_url if programme else None
        if track := find_track(parse_tracks(tracks), at):
            return NowPlaying(track.title, track.artist, image_url)
        if programme:
            return NowPlaying(programme.title, None, image_url)
        return NowPlaying(channel.name)

    async def _load(self, url: str) -> Any:
        async with self._locks[url]:
            cached = self._documents.get(url)
            if cached and self._clock() < cached[1]:
                return cached[0]
            document = cached[0] if cached else None
            try:
                document = await self._fetch_json(url)
            except NowPlayingError as err:
                self._logger.debug("Keeping the last answer: %s", err)
            self._documents[url] = (document, self._clock() + NOW_PLAYING_CACHE_SECONDS)
            return document
