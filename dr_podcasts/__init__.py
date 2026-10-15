"""DR Podcasts: search and play the DR podcasts that drpodcast.nu republishes as RSS."""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientTimeout
from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import (
    ContentType,
    ImageType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import MediaNotFoundError
from music_assistant_models.media_items import (
    AudioFormat,
    BrowseFolder,
    MediaItemImage,
    Podcast,
    PodcastEpisode,
    ProviderMapping,
    SearchResults,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails

from music_assistant.constants import CONF_ENTRY_LIBRARY_SYNC_PODCASTS
from music_assistant.helpers.podcast_parsers import (
    enrich_episode_chapters,
    find_episode_stream_url,
    get_cached_podcast,
    get_episode_positions,
    parse_podcast,
    parse_podcast_episode,
    refresh_cached_podcast,
)
from music_assistant.models.music_provider import MusicProvider

from .index import INDEX_URL, IndexEntry, parse_index, recently_updated, search_index

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Sequence

    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.media_items import ItemMapping, MediaItemType
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

PUBLISHER = "DR"
INDEX_CACHE_SECONDS = 12 * 3600
INDEX_RETRY_SECONDS = 10 * 60
INDEX_TIMEOUT = ClientTimeout(total=30)
FEED_CACHE_SECONDS = 15 * 60
BROWSE_RECENT = "recent"
BROWSE_ALL = "all"
BROWSE_FOLDERS = {BROWSE_RECENT: "Recently updated", BROWSE_ALL: "All podcasts"}
BROWSE_RECENT_COUNT = 50

SUPPORTED_FEATURES = {
    ProviderFeature.SEARCH,
    ProviderFeature.BROWSE,
    ProviderFeature.LIBRARY_PODCASTS,
}

CONF_ENTRY_LIBRARY_SYNC_PODCASTS_HIDDEN = ConfigEntry.from_dict(
    {
        **CONF_ENTRY_LIBRARY_SYNC_PODCASTS.to_dict(),
        "hidden": True,
        "default_value": True,
    }
)


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return DRPodcastsProvider(mass, manifest, config, SUPPORTED_FEATURES)


class DRPodcastsProvider(MusicProvider):
    """Music provider for the DR podcasts republished by drpodcast.nu."""

    _index: list[IndexEntry]
    _index_expires: float
    _index_lock: asyncio.Lock

    @property
    def max_concurrent_streams(self) -> None:
        """Allow unlimited concurrent upstream source streams."""
        return None

    @property
    def is_streaming_provider(self) -> bool:
        """Return True if the provider is a streaming provider."""
        return True

    async def get_config_entries(self) -> tuple[ConfigEntry, ...]:
        """Return Config entries to setup this provider."""
        return (CONF_ENTRY_LIBRARY_SYNC_PODCASTS_HIDDEN,)

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        self._index = []
        self._index_expires = 0.0
        self._index_lock = asyncio.Lock()

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 5
    ) -> SearchResults:
        """Search the title and description of every DR podcast."""
        result = SearchResults()
        if MediaType.PODCAST not in media_types:
            return result
        entries = search_index(await self._get_index(), search_query, limit)
        result.podcasts = [self._build_podcast(entry) for entry in entries]
        return result

    async def browse(self, path: str) -> Sequence[MediaItemType | ItemMapping | BrowseFolder]:
        """Browse the DR podcasts by latest episode or by title."""
        folder = path.split("://", 1)[1].strip("/")
        if not folder:
            return [
                BrowseFolder(
                    item_id=folder_id,
                    provider=self.instance_id,
                    path=f"{self.instance_id}://{folder_id}",
                    name=name,
                )
                for folder_id, name in BROWSE_FOLDERS.items()
            ]
        index = await self._get_index()
        if folder == BROWSE_RECENT:
            entries = recently_updated(index, BROWSE_RECENT_COUNT)
        elif folder == BROWSE_ALL:
            entries = sorted(index, key=lambda entry: entry.title.casefold())
        else:
            raise KeyError("Invalid subpath")
        return [self._build_podcast(entry) for entry in entries]

    async def get_library_podcasts(self) -> AsyncGenerator[Podcast]:
        """
        Get library podcasts.

        The provider has no library of its own, so this refreshes the feeds of the podcasts
        that were added to the MA library from search or browse. The item_id is the feed url.
        """
        podcasts = await self.mass.music.podcasts.get_library_items_by_prov_id(
            provider_instance=self.instance_id
        )
        for podcast in podcasts:
            our_provider_mapping: ProviderMapping | None = None
            for provider_mapping in podcast.provider_mappings:
                if provider_mapping.provider_instance == self.instance_id:
                    our_provider_mapping = provider_mapping
                    break
            if our_provider_mapping is None:
                self.logger.error("Podcast %s lacks a provider mapping.", podcast.name)
                continue
            feed_url = our_provider_mapping.item_id
            try:
                parsed_podcast = await refresh_cached_podcast(
                    mass=self.mass,
                    provider_instance_id=self.instance_id,
                    feed_url=feed_url,
                    cache_expiration=FEED_CACHE_SECONDS,
                )
            except MediaNotFoundError:
                # The sync drops podcasts it is not given, which a feed outage must not cause.
                self.logger.warning("Was unable to sync podcast %s (%s).", podcast.name, feed_url)
                podcast.item_id = feed_url
                podcast.provider_mappings = {our_provider_mapping}
                yield podcast
                continue
            yield parse_podcast(
                feed_url=feed_url,
                parsed_feed=parsed_podcast,
                instance_id=self.instance_id,
                domain=self.domain,
            )

    async def get_podcast(self, prov_podcast_id: str) -> Podcast:
        """Get podcast."""
        parsed = await self._cache_get_podcast(prov_podcast_id)
        return parse_podcast(
            feed_url=prov_podcast_id,
            parsed_feed=parsed,
            instance_id=self.instance_id,
            domain=self.domain,
        )

    async def get_podcast_episodes(self, prov_podcast_id: str) -> AsyncGenerator[PodcastEpisode]:
        """Get podcast episodes."""
        podcast = await self._cache_get_podcast(prov_podcast_id)
        podcast_cover = podcast.get("cover_url")
        episodes = podcast.get("episodes", [])
        positions = get_episode_positions(episodes)
        for position, episode in zip(positions, episodes, strict=True):
            if mass_episode := parse_podcast_episode(
                episode=episode,
                prov_podcast_id=prov_podcast_id,
                position=position,
                podcast_cover=podcast_cover,
                podcast_name=podcast.get("title"),
                domain=self.domain,
                instance_id=self.instance_id,
            ):
                yield mass_episode

    async def get_podcast_episode(self, prov_episode_id: str) -> PodcastEpisode:
        """Get single podcast episode."""
        podcast_id, guid_or_stream_url = prov_episode_id.split(" ")
        podcast = await self._cache_get_podcast(podcast_id)
        podcast_cover = podcast.get("cover_url")
        episodes = podcast.get("episodes", [])
        positions = get_episode_positions(episodes)
        for position, episode in zip(positions, episodes, strict=True):
            mass_episode = parse_podcast_episode(
                episode=episode,
                prov_podcast_id=podcast_id,
                position=position,
                podcast_cover=podcast_cover,
                podcast_name=podcast.get("title"),
                domain=self.domain,
                instance_id=self.instance_id,
            )
            if mass_episode is None:
                continue
            _, _guid_or_stream_url = mass_episode.item_id.split(" ")
            if guid_or_stream_url == _guid_or_stream_url:
                await enrich_episode_chapters(
                    session=self.mass.http_session,
                    chapters_json_url=episode.get("chapters_json_url"),
                    mass_episode=mass_episode,
                )
                return mass_episode
        raise MediaNotFoundError("Episode not found")

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Get streamdetails for item."""
        podcast_id, guid_or_stream_url = item_id.split(" ")
        parsed_podcast = await self._cache_get_podcast(podcast_id)
        stream_url = find_episode_stream_url(
            parsed_feed=parsed_podcast, guid_or_stream_url=guid_or_stream_url
        )
        if stream_url is None:
            raise MediaNotFoundError
        return StreamDetails(
            provider=self.instance_id,
            item_id=item_id,
            audio_format=AudioFormat(
                content_type=ContentType.try_parse(stream_url),
            ),
            media_type=MediaType.PODCAST_EPISODE,
            stream_type=StreamType.HTTP,
            path=stream_url,
            can_seek=True,
            allow_seek=True,
        )

    async def _get_index(self) -> list[IndexEntry]:
        """Return the podcast index, refreshing it when older than INDEX_CACHE_SECONDS."""
        async with self._index_lock:
            if self._index and time.monotonic() < self._index_expires:
                return self._index
            try:
                entries = await self._fetch_index()
            except MediaNotFoundError as err:
                if not self._index:
                    raise
                self.logger.warning("Could not refresh the DR podcast index, keeping it: %s", err)
                self._index_expires = time.monotonic() + INDEX_RETRY_SECONDS
                return self._index
            self._index = entries
            self._index_expires = time.monotonic() + INDEX_CACHE_SECONDS
            return entries

    async def _fetch_index(self) -> list[IndexEntry]:
        try:
            async with self.mass.http_session.get(
                INDEX_URL, raise_for_status=True, timeout=INDEX_TIMEOUT
            ) as response:
                html = await response.text()
        except (ClientError, TimeoutError) as err:
            raise MediaNotFoundError(f"Could not load the DR podcast index: {err}") from err
        entries = await asyncio.to_thread(parse_index, html)
        if not entries:
            raise MediaNotFoundError("The DR podcast index lists no podcasts.")
        return entries

    def _build_podcast(self, entry: IndexEntry) -> Podcast:
        """Build a podcast from an index entry; the full details come from the feed."""
        feed_url = entry.feed_url
        podcast = Podcast(
            item_id=feed_url,
            name=entry.title,
            publisher=PUBLISHER,
            provider=self.instance_id,
            total_episodes=entry.episodes,
            provider_mappings={
                ProviderMapping(
                    item_id=feed_url,
                    provider_domain=self.domain,
                    provider_instance=self.instance_id,
                )
            },
        )
        podcast.metadata.description = entry.description
        podcast.metadata.images = UniqueList(
            [
                MediaItemImage(
                    type=ImageType.THUMB,
                    path=entry.thumb_url,
                    provider=self.instance_id,
                    remotely_accessible=True,
                )
            ]
        )
        return podcast

    async def _cache_get_podcast(self, prov_podcast_id: str) -> dict[str, Any]:
        # raises MediaNotFoundError if the feed is gone or invalid
        return await get_cached_podcast(
            mass=self.mass,
            provider_instance_id=self.instance_id,
            feed_url=prov_podcast_id,
            cache_expiration=FEED_CACHE_SECONDS,
        )
