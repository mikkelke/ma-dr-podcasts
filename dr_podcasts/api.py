"""
DR's radio API, https://api.dr.dk/radio/v5, the only source of this provider.

It is the API that DR's own web app calls, and it needs no login. API_HEADERS carries
the public x-apikey that the web app sends with its requests. Documents are JSON. A list comes as
{"items": [...], "totalSize": n, "next": url}, where next is only there while there is more, and
is paged with the limit and offset parameters. An item that DR does not know answers 404.

Pictures are served by asset.dr.dk and are named by the id of an image asset (id, target, ratio),
which IMAGE_URL turns into a square picture of 512 pixels.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

API_URL = "https://api.dr.dk/radio/v5"
IMAGE_URL = (
    "https://asset.dr.dk/drlyd/images/{}"
    "?im=AspectCrop%3D%28512%2C512%29%2CxPosition%3D.5%2CyPosition%3D.5%3BResize%3D%28512%2C512%29"
)
API_HEADERS = {"Accept": "application/json", "x-apikey": "6Wkh8s98Afx1ZAaTT4FuWODTmvWGDPpR"}
RETRY_SECONDS = 10 * 60


class ApiError(Exception):
    """DR's radio API could not be reached or answered with something unusable."""


class NotFoundError(ApiError):
    """DR's radio API does not know what was asked for."""


def clean_text(value: Any) -> str | None:
    """Return the value stripped, if it is text with something in it."""
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_time(value: Any) -> float | None:
    """Return the epoch seconds of an ISO 8601 time that carries a UTC offset, else None."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment.timestamp() if moment.tzinfo else None


def image_url(assets: Any, *targets: str) -> str | None:
    """
    Return the url of the square picture among the image assets, if any.

    A picture whose target is named earlier in targets wins; the others follow, ordered by id.
    """
    candidates = []
    for asset in assets if isinstance(assets, list) else []:
        if not isinstance(asset, dict) or asset.get("ratio") != "1:1":
            continue
        if image_id := clean_text(asset.get("id")):
            target = asset.get("target")
            rank = targets.index(target) if target in targets else len(targets)
            candidates.append((rank, image_id))
    return IMAGE_URL.format(min(candidates)[1]) if candidates else None


class Cache[V]:
    """
    Loads values from DR's radio API by key and remembers them.

    A value is kept for ttl seconds. One that cannot be reloaded is served from the last good
    copy, which is tried again after RETRY_SECONDS; without a copy the error is raised. A value
    that DR no longer knows is dropped.
    """

    def __init__(
        self,
        name: str,
        load: Callable[[str], Awaitable[V]],
        ttl: float,
        logger: Logger,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize with a coroutine that loads the value of a key or raises ApiError."""
        self._name = name
        self._load = load
        self._ttl = ttl
        self._logger = logger
        self._clock = clock
        self._values: dict[str, tuple[V, float]] = {}
        self._locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def get(self, key: str = "") -> V:
        """Return the value of the key, loaded again once it has expired."""
        async with self._locks[key]:
            cached = self._values.get(key)
            if cached and self._clock() < cached[1]:
                return cached[0]
            try:
                value = await self._load(key)
            except NotFoundError:
                self._values.pop(key, None)
                raise
            except ApiError as err:
                if cached is None:
                    raise
                self._logger.warning(
                    "Could not refresh %s, keeping the last copy: %s",
                    f"{self._name} {key}".strip(),
                    err,
                )
                self._values[key] = (cached[0], self._clock() + RETRY_SECONDS)
                return cached[0]
            self._values[key] = (value, self._clock() + self._ttl)
            return value
