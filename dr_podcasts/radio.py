"""
The DR live radio channels and what they are playing, from DR's radio API (see api.py).

Three documents are read, as JSON:

- GET /channels lists the channels. Per channel the fields used are slug, title, id, isFlowChannel,
  isDistrict, audioAssets (format, url, bitrate) and channelLogos or imageAssets (id, target,
  ratio). P4 and P5 carry their districts (P4 København, P5 København...) in districts, each of
  them a channel of its own with a stream of its own.
- GET /indexpoints/live/urn:dr:radio:channel:<id> lists the tracks the channel has played,
  newest first. The id is the last part of the channel's id in the channels document, so every
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
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .api import (
    API_URL,
    ApiError,
    Cache,
    clean_text,
    image_url,
    parse_time,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from logging import Logger

CHANNELS_URL = f"{API_URL}/channels"
TRACKS_URL = f"{API_URL}/indexpoints/live/{{}}"
PROGRAMMES_URL = f"{API_URL}/schedules/all/now"
CHANNELS_CACHE_SECONDS = 12 * 3600
NOW_PLAYING_CACHE_SECONDS = 30
STREAM_DELAY_SECONDS = 30
STREAM_METADATA_UPDATE_INTERVAL = 10


@dataclass(frozen=True, slots=True)
class Channel:
    """One DR radio channel that plays a live stream."""

    slug: str
    name: str
    urn: str
    stream_url: str
    hls: bool
    image_url: str | None
    district: bool

    @property
    def tracks_url(self) -> str:
        """Return the url of the list of tracks the channel has played."""
        return TRACKS_URL.format(self.urn)


def _channel_name(slug: str, title: str) -> str:
    """
    Return the name of a channel, DR P3 for P3.

    DR's title of P6 and P8 lacks what their slugs say: p6beat is DR P6 Beat, p8jazz is DR P8 Jazz.
    """
    compact = title.replace(" ", "").casefold()
    suffix = slug[len(compact) :] if slug.casefold().startswith(compact) else ""
    return " ".join(part for part in ("DR", title, suffix.capitalize()) if part)


def _stream(assets: Any) -> tuple[str, bool] | None:
    """Return the url of the stream to play and whether it is HLS: the HLS one, else best ICY."""
    hls: list[str] = []
    icy: list[tuple[float, str]] = []
    for asset in assets if isinstance(assets, list) else []:
        url = clean_text(asset.get("url")) if isinstance(asset, dict) else None
        if not url or not url.startswith(("https://", "http://")):
            continue
        if asset.get("format") == "HLS":
            hls.append(url)
        elif asset.get("format") == "ICY":
            bitrate = asset.get("bitrate")
            icy.append((bitrate if isinstance(bitrate, int | float) else 0, url))
    if hls:
        return hls[0], True
    return (max(icy, key=lambda asset: asset[0])[1], False) if icy else None


def _parse_channel(item: Any) -> Channel | None:
    """Return the channel an item of the channels document describes, if it plays a stream."""
    if not isinstance(item, dict) or not item.get("isFlowChannel"):
        return None
    slug = clean_text(item.get("slug"))
    title = clean_text(item.get("title"))
    urn = clean_text(item.get("id"))
    stream = _stream(item.get("audioAssets"))
    if slug is None or title is None or urn is None or stream is None:
        return None
    picture = image_url(item.get("channelLogos")) or image_url(
        item.get("imageAssets"), "SquareImage"
    )
    return Channel(
        slug,
        _channel_name(slug, title),
        urn,
        stream[0],
        stream[1],
        picture,
        bool(item.get("isDistrict")),
    )


def parse_channels(document: Any) -> list[Channel]:
    """
    Return the live radio channels of a channels document, in DR's order.

    Like the web app, this lists the districts of P4 and P5 in place of P4 and P5 themselves, and
    only the flow channels, which are the stations that play round the clock. The rest of the
    channels are events and web feeds, or have no stream.
    """
    channels: dict[str, Channel] = {}
    for entry in document if isinstance(document, list) else []:
        districts = entry.get("districts") if isinstance(entry, dict) else None
        for item in districts if isinstance(districts, list) and districts else [entry]:
            if channel := _parse_channel(item):
                channels.setdefault(channel.slug, channel)
    return list(channels.values())


def search_channels(channels: Iterable[Channel], query: str, limit: int) -> list[Channel]:
    """Return the channels whose name holds every word of the query, ignoring case."""
    words = query.casefold().split()
    if not words:
        return []
    matches = [
        channel for channel in channels if all(word in channel.name.casefold() for word in words)
    ]
    return matches[:limit]


class ChannelDirectory:
    """
    The live radio channels, read from DR's channels document and remembered.

    The document is kept for CHANNELS_CACHE_SECONDS. When it cannot be read the last one that was
    read is used, which keeps the stream urls of the channels playing.
    """

    def __init__(
        self,
        fetch_json: Callable[[str], Awaitable[Any]],
        logger: Logger,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a coroutine that returns the JSON at a url or raises ApiError."""
        self._fetch_json = fetch_json
        self._cache = Cache(
            "the DR radio channels", self._load, CHANNELS_CACHE_SECONDS, logger, clock
        )

    async def channels(self) -> list[Channel]:
        """Return the channels that play a stream."""
        return await self._cache.get()

    async def find(self, slug: str) -> Channel | None:
        """Return the channel with the slug, if DR lists it."""
        return next((channel for channel in await self.channels() if channel.slug == slug), None)

    async def _load(self, _key: str) -> list[Channel]:
        channels = parse_channels(await self._fetch_json(CHANNELS_URL))
        if not channels:
            raise ApiError("DR lists no radio channels.")
        return channels


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


def _composers(item: dict[str, Any]) -> list[str]:
    """Return the names of the composers among the roles of a track, each one once."""
    roles = item.get("roles")
    names = [
        clean_text(role.get("name"))
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
    return clean_text(item.get("description"))


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
        title = clean_text(item.get("title"))
        start = parse_time(item.get("playedTime"))
        duration = item.get("durationMilliseconds")
        if title is None or start is None or not isinstance(duration, int | float) or duration <= 0:
            continue
        tracks.append(Track(title, _artist(item), start, start + duration / 1000))
    return tracks


def find_track(tracks: Iterable[Track], at: float) -> Track | None:
    """Return the track playing at the time, which is the latest one to have started."""
    playing = [track for track in tracks if track.start <= at < track.end]
    return max(playing, key=lambda track: track.start, default=None)


def find_programme(document: Any, channel: Channel, at: float) -> Programme | None:
    """Return the programme that the schedules document has on air on the channel at the time."""
    entries = document if isinstance(document, list) else []
    for entry in entries:
        info = entry.get("channel") if isinstance(entry, dict) else None
        if not isinstance(info, dict) or info.get("slug") != channel.slug:
            continue
        title = clean_text(entry.get("title"))
        end = parse_time(entry.get("endTime"))
        if title is None or end is None or end <= at:
            return None
        return Programme(title, image_url(entry.get("imageAssets"), "SquareImage"), end)
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
        """Initialize with a coroutine that returns the JSON at a url or raises ApiError."""
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
        picture = programme.image_url if programme else None
        if track := find_track(parse_tracks(tracks), at):
            return NowPlaying(track.title, track.artist, picture)
        if programme:
            return NowPlaying(programme.title, None, picture)
        return NowPlaying(channel.name)

    async def _load(self, url: str) -> Any:
        async with self._locks[url]:
            cached = self._documents.get(url)
            if cached and self._clock() < cached[1]:
                return cached[0]
            document = cached[0] if cached else None
            try:
                document = await self._fetch_json(url)
            except ApiError as err:
                self._logger.debug("Keeping the last answer: %s", err)
            self._documents[url] = (document, self._clock() + NOW_PLAYING_CACHE_SECONDS)
            return document
