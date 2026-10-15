"""The podcast index published at drpodcast.nu."""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

BASE_URL = "https://drpodcast.nu"
INDEX_URL = f"{BASE_URL}/"


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One podcast row of the index."""

    slug: str
    title: str
    episodes: int
    latest: str
    description: str

    @property
    def feed_url(self) -> str:
        """Return the RSS feed url."""
        return f"{BASE_URL}/{self.slug}/feed.xml"

    @property
    def thumb_url(self) -> str:
        """Return the url of the small cover image."""
        return f"{BASE_URL}/{self.slug}/thumb.jpg"


class _RowParser(HTMLParser):
    """Collect the podcast rows, which carry their data as data-* attributes."""

    def __init__(self) -> None:
        super().__init__()
        self.entries: list[IndexEntry] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Turn a podcast row into an entry."""
        if tag != "tr":
            return
        data = {key: value or "" for key, value in attrs}
        slug = data.get("data-slug")
        title = data.get("data-title")
        if not slug or not title:
            return
        episodes = data.get("data-episodes", "")
        self.entries.append(
            IndexEntry(
                slug=slug,
                title=title,
                episodes=int(episodes) if episodes.isdigit() else 0,
                latest=data.get("data-latest", ""),
                description=data.get("data-description", ""),
            )
        )


def parse_index(html: str) -> list[IndexEntry]:
    """Parse the index page into its podcast entries."""
    parser = _RowParser()
    parser.feed(html)
    parser.close()
    return parser.entries


def search_index(entries: list[IndexEntry], query: str, limit: int) -> list[IndexEntry]:
    """
    Return the entries matching all words of the query in title or description.

    Matching is case-insensitive. Titles starting with the query come first, then other
    title matches, then description matches; each group is ordered by title.
    """
    folded_query = " ".join(query.casefold().split())
    words = folded_query.split()
    if not words:
        return []
    ranked: list[tuple[int, str, IndexEntry]] = []
    for entry in entries:
        title = entry.title.casefold()
        if all(word in title for word in words):
            rank = 0 if title.startswith(folded_query) else 1
        elif all(word in f"{title}\n{entry.description.casefold()}" for word in words):
            rank = 2
        else:
            continue
        ranked.append((rank, title, entry))
    ranked.sort(key=lambda item: item[:2])
    return [entry for _, _, entry in ranked[:limit]]


def recently_updated(entries: list[IndexEntry], limit: int) -> list[IndexEntry]:
    """Return the podcasts with episodes whose latest episode is the most recent."""
    published = [entry for entry in entries if entry.episodes > 0 and entry.latest]
    published.sort(key=lambda entry: entry.latest, reverse=True)
    return published[:limit]
