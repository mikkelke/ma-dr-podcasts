"""DR Podcasts: the DR podcasts and the DR live radio channels, from DR's radio API."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientTimeout
from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import (
    ContentType,
    ImageType,
    LinkType,
    MediaType,
    ProviderFeature,
    StreamType,
)
from music_assistant_models.errors import MediaNotFoundError, ProviderUnavailableError
from music_assistant_models.media_items import (
    AudioFormat,
    BrowseFolder,
    ItemMapping,
    MediaItemImage,
    MediaItemLink,
    Podcast,
    PodcastEpisode,
    ProviderMapping,
    Radio,
    SearchResults,
    UniqueList,
)
from music_assistant_models.streamdetails import StreamDetails, StreamMetadata

from music_assistant.constants import (
    CONF_ENTRY_LIBRARY_SYNC_PODCASTS,
    CONF_ENTRY_LIBRARY_SYNC_RADIOS,
)
from music_assistant.helpers.podcast_parsers import rank_episodes_by_date
from music_assistant.models.music_provider import MusicProvider

from .api import API_HEADERS, ApiError, NotFoundError
from .podcasts import Episode, PodcastDirectory, Series, recently_updated, search_series
from .radio import (
    STREAM_METADATA_UPDATE_INTERVAL,
    Channel,
    ChannelDirectory,
    NowPlayingResolver,
    search_channels,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator, Sequence

    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.media_items import MediaItemType
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType

PUBLISHER = "DR"
API_TIMEOUT = ClientTimeout(total=30)
NOW_PLAYING_TIMEOUT = ClientTimeout(total=10)
BROWSE_RECENT = "recent"
BROWSE_ALL = "all"
BROWSE_RADIO = "radio"
BROWSE_FOLDERS = {
    BROWSE_RECENT: "Recently updated",
    BROWSE_ALL: "All podcasts",
    BROWSE_RADIO: "Live radio",
}
BROWSE_RECENT_COUNT = 50

SUPPORTED_FEATURES = {
    ProviderFeature.SEARCH,
    ProviderFeature.BROWSE,
    ProviderFeature.LIBRARY_PODCASTS,
    ProviderFeature.LIBRARY_RADIOS,
}

CONF_ENTRY_LIBRARY_SYNC_PODCASTS_HIDDEN = ConfigEntry.from_dict(
    {
        **CONF_ENTRY_LIBRARY_SYNC_PODCASTS.to_dict(),
        "hidden": True,
        "default_value": True,
    }
)
CONF_ENTRY_LIBRARY_SYNC_RADIOS_HIDDEN = ConfigEntry.from_dict(
    {
        **CONF_ENTRY_LIBRARY_SYNC_RADIOS.to_dict(),
        "hidden": True,
        "default_value": True,
    }
)


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return DRPodcastsProvider(mass, manifest, config, SUPPORTED_FEATURES)


@contextmanager
def _api_errors() -> Iterator[None]:
    """Raise the errors of Music Assistant in place of the errors of DR's radio API."""
    try:
        yield
    except NotFoundError as err:
        raise MediaNotFoundError(str(err)) from err
    except ApiError as err:
        raise ProviderUnavailableError(str(err)) from err


class DRPodcastsProvider(MusicProvider):
    """Music provider for the DR podcasts and the DR live radio, from DR's radio API."""

    _channels: ChannelDirectory
    _podcasts: PodcastDirectory
    _now_playing: NowPlayingResolver

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
        return (CONF_ENTRY_LIBRARY_SYNC_PODCASTS_HIDDEN, CONF_ENTRY_LIBRARY_SYNC_RADIOS_HIDDEN)

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        self._channels = ChannelDirectory(self._get_json, self.logger)
        self._podcasts = PodcastDirectory(self._get_json, self.logger)
        self._now_playing = NowPlayingResolver(self._get_now_playing, self.logger)

    async def search(
        self, search_query: str, media_types: list[MediaType], limit: int = 5
    ) -> SearchResults:
        """Search the title and description of every DR podcast and the name of every channel."""
        result = SearchResults()
        with _api_errors():
            if MediaType.PODCAST in media_types:
                series = search_series(await self._podcasts.catalogue(), search_query, limit)
                result.podcasts = [self._build_podcast(entry) for entry in series]
            if MediaType.RADIO in media_types:
                channels = search_channels(await self._channels.channels(), search_query, limit)
                result.radio = [self._build_radio(channel) for channel in channels]
        return result

    async def browse(self, path: str) -> Sequence[MediaItemType | ItemMapping | BrowseFolder]:
        """Browse the DR podcasts by latest episode or by title, and the live radio channels."""
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
        if folder not in BROWSE_FOLDERS:
            raise KeyError("Invalid subpath")
        with _api_errors():
            if folder == BROWSE_RADIO:
                return [self._build_radio(channel) for channel in await self._channels.channels()]
            catalogue = await self._podcasts.catalogue()
        if folder == BROWSE_RECENT:
            series = recently_updated(catalogue, BROWSE_RECENT_COUNT)
        else:
            series = sorted(catalogue, key=lambda entry: entry.title.casefold())
        return [self._build_podcast(entry) for entry in series]

    async def get_library_podcasts(self) -> AsyncGenerator[Podcast]:
        """
        Get library podcasts.

        The provider has no library of its own, so this brings up to date the podcasts that were
        added to the MA library from search or browse. The item_id is the id of the podcast at DR.
        A podcast that DR no longer has is left out, which removes it from the library.
        """
        saved = await self.mass.music.podcasts.get_library_items_by_prov_id(
            provider_instance=self.instance_id
        )
        for podcast_id in self._saved_item_ids(saved):
            try:
                with _api_errors():
                    series = await self._podcasts.series(podcast_id)
            except MediaNotFoundError as err:
                self.logger.warning("Leaving podcast %s out of the library: %s", podcast_id, err)
                continue
            except ProviderUnavailableError as err:
                self.report_skipped_sync_item(MediaType.PODCAST, podcast_id, err)
                continue
            yield self._build_podcast(series)

    async def get_library_radios(self) -> AsyncGenerator[Radio]:
        """
        Get library radios.

        These are the national channels and the district channels that were added to the MA
        library from search or browse.
        """
        saved = await self.mass.music.radio.get_library_items_by_prov_id(
            provider_instance=self.instance_id
        )
        saved_slugs = set(self._saved_item_ids(saved))
        with _api_errors():
            channels = await self._channels.channels()
        for channel in channels:
            if not channel.district or channel.slug in saved_slugs:
                yield self._build_radio(channel)

    async def get_radio(self, prov_radio_id: str) -> Radio:
        """Get radio."""
        return self._build_radio(await self._get_channel(prov_radio_id))

    async def get_podcast(self, prov_podcast_id: str) -> Podcast:
        """Get podcast."""
        with _api_errors():
            return self._build_podcast(await self._podcasts.series(prov_podcast_id))

    async def get_podcast_episodes(self, prov_podcast_id: str) -> AsyncGenerator[PodcastEpisode]:
        """Get podcast episodes."""
        with _api_errors():
            series = await self._podcasts.series(prov_podcast_id)
            episodes = await self._podcasts.episodes(prov_podcast_id)
        podcast = self._podcast_reference(series.id, series.title)
        positions = rank_episodes_by_date([episode.published for episode in episodes])
        for position, episode in zip(positions, episodes, strict=True):
            yield self._build_episode(episode, podcast, series.image_url, position)

    async def get_podcast_episode(self, prov_episode_id: str) -> PodcastEpisode:
        """Get single podcast episode, in the podcast DR names for it and at position 0."""
        with _api_errors():
            episode = await self._podcasts.episode(prov_episode_id)
        if episode.series_id is None:
            raise MediaNotFoundError(f"DR names no podcast for episode {prov_episode_id}.")
        podcast = self._podcast_reference(episode.series_id, episode.series_title or episode.title)
        return self._build_episode(episode, podcast, None, 0)

    async def get_stream_details(self, item_id: str, media_type: MediaType) -> StreamDetails:
        """Get streamdetails for item."""
        if media_type == MediaType.RADIO:
            return self._radio_stream_details(await self._get_channel(item_id))
        with _api_errors():
            episode = await self._podcasts.episode(item_id)
        return StreamDetails(
            provider=self.instance_id,
            item_id=item_id,
            audio_format=AudioFormat(
                content_type=ContentType.AAC if episode.hls else ContentType.MP3
            ),
            media_type=MediaType.PODCAST_EPISODE,
            stream_type=StreamType.HLS if episode.hls else StreamType.HTTP,
            path=episode.audio_url,
            can_seek=True,
            allow_seek=True,
        )

    async def _get_json(self, url: str, timeout: ClientTimeout = API_TIMEOUT) -> Any:
        """Return the JSON at the url on DR's radio API, else raise ApiError."""
        try:
            async with self.mass.http_session.get(
                url, headers=API_HEADERS, timeout=timeout
            ) as response:
                if response.status == HTTPStatus.NOT_FOUND:
                    raise NotFoundError(f"DR has nothing at {url}.")
                response.raise_for_status()
                return await response.json()
        except (ClientError, TimeoutError, ValueError) as err:
            raise ApiError(f"Could not load {url}: {str(err) or type(err).__name__}") from err

    async def _get_now_playing(self, url: str) -> Any:
        return await self._get_json(url, NOW_PLAYING_TIMEOUT)

    async def _update_stream_metadata(
        self, stream_details: StreamDetails, elapsed_time: int
    ) -> None:
        """Show what DR is playing on the channel; the player queue controller calls this."""
        try:
            channel = await self._channels.find(stream_details.item_id)
        except ApiError:
            return
        if channel is None:
            return
        playing = await self._now_playing.resolve(channel)
        stream_details.stream_metadata = StreamMetadata(
            title=playing.title, artist=playing.artist, image_url=playing.image_url
        )

    async def _get_channel(self, slug: str) -> Channel:
        with _api_errors():
            channel = await self._channels.find(slug)
        if channel is None:
            raise MediaNotFoundError(f"Unknown DR radio channel: {slug}")
        return channel

    def _saved_item_ids(self, saved: Sequence[MediaItemType]) -> list[str]:
        """Return the ids at this provider of the library items that are in the library."""
        return [
            mapping.item_id
            for item in saved
            for mapping in item.provider_mappings
            if mapping.provider_instance == self.instance_id and mapping.in_library
        ]

    def _mapping(self, item_id: str, **details: Any) -> ProviderMapping:
        return ProviderMapping(
            item_id=item_id,
            provider_domain=self.domain,
            provider_instance=self.instance_id,
            **details,
        )

    def _thumbnails(self, url: str | None) -> UniqueList[MediaItemImage]:
        if not url:
            return UniqueList()
        return UniqueList(
            [
                MediaItemImage(
                    type=ImageType.THUMB,
                    path=url,
                    provider=self.instance_id,
                    remotely_accessible=True,
                )
            ]
        )

    def _podcast_reference(self, podcast_id: str, name: str) -> ItemMapping:
        return ItemMapping(
            item_id=podcast_id,
            provider=self.instance_id,
            name=name,
            media_type=MediaType.PODCAST,
        )

    def _radio_stream_details(self, channel: Channel) -> StreamDetails:
        if channel.hls:
            stream_type, content_type = StreamType.HLS, ContentType.AAC
        else:
            stream_type, content_type = StreamType.ICY, ContentType.try_parse(channel.stream_url)
        return StreamDetails(
            provider=self.instance_id,
            item_id=channel.slug,
            audio_format=AudioFormat(content_type=content_type, channels=2),
            media_type=MediaType.RADIO,
            stream_type=stream_type,
            path=channel.stream_url,
            can_seek=False,
            allow_seek=False,
            stream_metadata_update_callback=self._update_stream_metadata,
            stream_metadata_update_interval=STREAM_METADATA_UPDATE_INTERVAL,
        )

    def _build_radio(self, channel: Channel) -> Radio:
        radio = Radio(
            item_id=channel.slug,
            name=channel.name,
            provider=self.instance_id,
            provider_mappings={self._mapping(channel.slug)},
        )
        radio.metadata.images = self._thumbnails(channel.image_url)
        return radio

    def _build_podcast(self, series: Series) -> Podcast:
        podcast = Podcast(
            item_id=series.id,
            name=series.title,
            publisher=PUBLISHER,
            provider=self.instance_id,
            total_episodes=series.episodes,
            provider_mappings={self._mapping(series.id)},
        )
        podcast.metadata.description = series.description
        podcast.metadata.explicit = series.explicit
        podcast.metadata.genres = set(series.genres)
        podcast.metadata.images = self._thumbnails(series.image_url)
        if series.page_url:
            podcast.metadata.links = {MediaItemLink(type=LinkType.WEBSITE, url=series.page_url)}
        return podcast

    def _build_episode(
        self, episode: Episode, podcast: ItemMapping, cover: str | None, position: int
    ) -> PodcastEpisode:
        """Build an episode of the podcast; the cover is used if the episode has none."""
        built = PodcastEpisode(
            item_id=episode.id,
            provider=self.instance_id,
            name=episode.title,
            duration=episode.duration,
            position=position,
            podcast=podcast,
            provider_mappings={
                self._mapping(
                    episode.id,
                    audio_format=AudioFormat(
                        content_type=ContentType.AAC if episode.hls else ContentType.MP3
                    ),
                    url=episode.audio_url,
                )
            },
        )
        if episode.published is not None:
            built.metadata.release_date = datetime.fromtimestamp(episode.published, tz=UTC)
        built.metadata.description = episode.description
        built.metadata.explicit = episode.explicit
        built.metadata.images = self._thumbnails(episode.image_url or cover)
        if episode.page_url:
            built.metadata.links = {MediaItemLink(type=LinkType.WEBSITE, url=episode.page_url)}
        return built
