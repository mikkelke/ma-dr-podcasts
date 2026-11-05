"""
The DR podcasts: the series on DR's radio API (see api.py) and their episodes.

Four documents are read, as JSON:

- GET /series?limit=<n> lists all the series, about 700, <n> at a time, with a next url while
  there are more. Per item the fields used are productionNumber, title, description,
  numberOfEpisodes, latestEpisodeStartTime, imageAssets (id, target, ratio), categories,
  explicitContent, presentationUrl and isAvailableOnDemand.
- GET /series/<productionNumber> describes one series, with the fields of an item above.
- GET /series/<productionNumber>/episodes/groups?limit=<n> lists the episodes in groups, a season
  or a year each. Every group holds its first <n> episodes and has a next url while there are
  more. The plain /episodes list is not used, as it drops episodes of the series that are made
  of other series. Per episode the fields used are productionNumber, title, description,
  startTime, publishTime, durationMilliseconds, audioAssets (target, format, bitrate, url),
  imageAssets, explicitContent, presentationUrl, isAvailableOnDemand and series.
- GET /episodes/<productionNumber> describes one episode, with the fields of an episode above.

The production number is DR's own id of a series or an episode. It is what the web addresses of
DR's web app end in and what the API takes in place of its internal ids, so it stays the same
when a title changes. It is the id of the podcast or the episode in Music Assistant.

An episode is played from its progressive mp3 with the highest bitrate, else from its HLS stream.
The audio urls are links on DR's API that redirect to the file, with no key needed.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .api import (
    API_URL,
    ApiError,
    Cache,
    NotFoundError,
    clean_text,
    image_url,
    parse_time,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
    from logging import Logger

ID_PATTERN = re.compile(r"[0-9]+")
PAGE_SIZE = 500
MAX_PAGES = 100
CATALOGUE_CACHE_SECONDS = 3600
PODCAST_CACHE_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class Series:
    """One DR podcast."""

    id: str
    title: str
    description: str | None
    episodes: int
    latest: float | None
    image_url: str | None
    genres: tuple[str, ...]
    explicit: bool
    page_url: str | None


@dataclass(frozen=True, slots=True)
class Episode:
    """One episode of a DR podcast, with the audio to play."""

    id: str
    title: str
    description: str | None
    duration: int
    published: float | None
    audio_url: str
    hls: bool
    image_url: str | None
    explicit: bool
    page_url: str | None
    series_id: str | None
    series_title: str | None


def _identifier(value: Any) -> str | None:
    """Return the value if it is a production number, which is made of digits, else None."""
    return value if isinstance(value, str) and ID_PATTERN.fullmatch(value) else None


def _require_id(item_id: str, kind: str) -> str:
    """Return the id, or raise NotFoundError when it cannot be the id of a DR podcast or episode."""
    if _identifier(item_id) is None:
        raise NotFoundError(f"{item_id!r} is not the id of a DR {kind}.")
    return item_id


def _items(document: Any) -> list[Any]:
    """Return the items of a list document, or none when it is not one."""
    items = document.get("items") if isinstance(document, dict) else None
    return items if isinstance(items, list) else []


def parse_series(item: Any) -> Series | None:
    """Return the series that an item of DR's series documents describes, if it is available."""
    if not isinstance(item, dict) or item.get("isAvailableOnDemand") is False:
        return None
    series_id = _identifier(item.get("productionNumber"))
    title = clean_text(item.get("title"))
    if series_id is None or title is None:
        return None
    episodes = item.get("numberOfEpisodes")
    categories = item.get("categories")
    genres = [clean_text(name) for name in categories] if isinstance(categories, list) else []
    return Series(
        id=series_id,
        title=title,
        description=clean_text(item.get("description")),
        episodes=max(episodes, 0) if isinstance(episodes, int) else 0,
        latest=parse_time(item.get("latestEpisodeStartTime")),
        image_url=image_url(item.get("imageAssets"), "Podcast", "SquareImage"),
        genres=tuple(dict.fromkeys(name for name in genres if name)),
        explicit=item.get("explicitContent") is True,
        page_url=clean_text(item.get("presentationUrl")),
    )


def _audio(assets: Any) -> tuple[str, bool] | None:
    """Return the url of the audio to play and whether it is HLS: the best mp3, else HLS."""
    mp3: list[tuple[float, str]] = []
    hls: list[str] = []
    for asset in assets if isinstance(assets, list) else []:
        url = clean_text(asset.get("url")) if isinstance(asset, dict) else None
        if not url or not url.startswith(("https://", "http://")):
            continue
        if asset.get("target") == "Progressive" and asset.get("format") == "mp3":
            bitrate = asset.get("bitrate")
            mp3.append((bitrate if isinstance(bitrate, int | float) else 0, url))
        elif asset.get("format") == "HLS":
            hls.append(url)
    if mp3:
        return max(mp3, key=lambda asset: asset[0])[1], False
    return (hls[0], True) if hls else None


def parse_episode(item: Any) -> Episode | None:
    """Return the episode that an item of DR's episode documents describes, if it can be played."""
    if not isinstance(item, dict) or item.get("isAvailableOnDemand") is False:
        return None
    episode_id = _identifier(item.get("productionNumber"))
    title = clean_text(item.get("title"))
    audio = _audio(item.get("audioAssets"))
    if episode_id is None or title is None or audio is None:
        return None
    series = item.get("series") if isinstance(item.get("series"), dict) else {}
    duration = item.get("durationMilliseconds")
    published = parse_time(item.get("startTime"))
    if published is None:
        published = parse_time(item.get("publishTime"))
    return Episode(
        id=episode_id,
        title=title,
        description=clean_text(item.get("description")),
        duration=int(duration / 1000) if isinstance(duration, int | float) and duration > 0 else 0,
        published=published,
        audio_url=audio[0],
        hls=audio[1],
        image_url=image_url(item.get("imageAssets"), "Podcast", "SquareImage"),
        explicit=item.get("explicitContent") is True,
        page_url=clean_text(item.get("presentationUrl")),
        series_id=_identifier(series.get("productionNumber")),
        series_title=clean_text(series.get("title")),
    )


def search_series(series: Iterable[Series], query: str, limit: int) -> list[Series]:
    """
    Return the series matching all words of the query in title or description.

    Matching is case-insensitive. Titles starting with the query come first, then other
    title matches, then description matches; each group is ordered by title.
    """
    folded_query = " ".join(query.casefold().split())
    words = folded_query.split()
    if not words:
        return []
    ranked: list[tuple[int, str, Series]] = []
    for entry in series:
        title = entry.title.casefold()
        if all(word in title for word in words):
            rank = 0 if title.startswith(folded_query) else 1
        elif all(word in f"{title}\n{(entry.description or '').casefold()}" for word in words):
            rank = 2
        else:
            continue
        ranked.append((rank, title, entry))
    ranked.sort(key=lambda item: item[:2])
    return [entry for _, _, entry in ranked[:limit]]


def recently_updated(series: Iterable[Series], limit: int) -> list[Series]:
    """Return the series whose latest episode is the newest, the newest series first."""
    published = [entry for entry in series if entry.episodes > 0 and entry.latest is not None]
    published.sort(key=lambda entry: entry.latest or 0.0, reverse=True)
    return published[:limit]


class PodcastDirectory:
    """
    The DR podcasts and their episodes, read from DR's radio API and remembered.

    The list of all the podcasts is kept for CATALOGUE_CACHE_SECONDS, and a podcast and its
    episodes for PODCAST_CACHE_SECONDS. What cannot be read again is served from the last copy.
    """

    def __init__(
        self,
        fetch_json: Callable[[str], Awaitable[Any]],
        logger: Logger,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a coroutine that returns the JSON at a url or raises ApiError."""
        self._fetch_json = fetch_json
        self._catalogue = Cache(
            "the DR podcast list", self._load_catalogue, CATALOGUE_CACHE_SECONDS, logger, clock
        )
        self._series = Cache(
            "the DR podcast", self._load_series, PODCAST_CACHE_SECONDS, logger, clock
        )
        self._episodes = Cache(
            "the episodes of the DR podcast",
            self._load_episodes,
            PODCAST_CACHE_SECONDS,
            logger,
            clock,
        )

    async def catalogue(self) -> list[Series]:
        """Return all the podcasts."""
        return await self._catalogue.get()

    async def series(self, series_id: str) -> Series:
        """Return the podcast, or raise NotFoundError when DR has none with the id."""
        return await self._series.get(_require_id(series_id, "podcast"))

    async def episodes(self, series_id: str) -> list[Episode]:
        """Return the episodes of the podcast, or raise NotFoundError when DR has no such one."""
        return await self._episodes.get(_require_id(series_id, "podcast"))

    async def episode(self, episode_id: str) -> Episode:
        """
        Return the episode, or raise NotFoundError when DR has none with the id or no audio for it.

        The episode is read from DR on every call, so that what is played is what DR serves now.
        """
        document = await self._fetch_json(
            f"{API_URL}/episodes/{_require_id(episode_id, 'episode')}"
        )
        if (episode := parse_episode(document)) is None:
            raise NotFoundError(f"DR has no audio for episode {episode_id}.")
        return episode

    async def _load_catalogue(self, _key: str) -> list[Series]:
        podcasts: dict[str, Series] = {}
        first = await self._fetch_json(f"{API_URL}/series?limit={PAGE_SIZE}")
        async for page in self._pages(first):
            for series in map(parse_series, _items(page)):
                if series:
                    podcasts.setdefault(series.id, series)
        if not podcasts:
            raise ApiError("DR lists no podcasts.")
        return list(podcasts.values())

    async def _load_series(self, series_id: str) -> Series:
        document = await self._fetch_json(f"{API_URL}/series/{series_id}")
        if (series := parse_series(document)) is None:
            raise NotFoundError(f"DR has no podcast {series_id} to listen to.")
        return series

    async def _load_episodes(self, series_id: str) -> list[Episode]:
        groups = await self._fetch_json(
            f"{API_URL}/series/{series_id}/episodes/groups?limit={PAGE_SIZE}"
        )
        if not isinstance(groups, list):
            raise ApiError(f"DR sent no episode groups for podcast {series_id}.")
        episodes: dict[str, Episode] = {}
        for group in groups:
            async for page in self._pages(group):
                for episode in map(parse_episode, _items(page)):
                    if episode:
                        episodes.setdefault(episode.id, episode)
        return list(episodes.values())

    async def _pages(self, first: Any) -> AsyncIterator[Any]:
        """Yield a page of a list and then the pages that follow it, up to MAX_PAGES."""
        page = first
        for number in range(1, MAX_PAGES + 1):
            yield page
            next_url = page.get("next") if isinstance(page, dict) else None
            if not next_url or not _items(page) or number == MAX_PAGES:
                return
            if not isinstance(next_url, str) or not next_url.startswith(f"{API_URL}/"):
                raise ApiError(f"DR sent a page that is not on its API: {next_url}")
            page = await self._fetch_json(next_url)
