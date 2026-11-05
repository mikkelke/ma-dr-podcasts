"""Shared helpers of the tests: the modules under test, the fixtures and stand-ins for DR."""

import asyncio
import importlib
import json
import sys
import types
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
PACKAGE = "dr_podcasts_under_test"
LOGGER_NAME = "dr_podcasts_test"


def _load(name):
    """Import a module of the provider package without importing the provider itself."""
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(ROOT / "dr_podcasts")]
        sys.modules[PACKAGE] = package
    return importlib.import_module(f"{PACKAGE}.{name}")


api = _load("api")
radio = _load("radio")
podcasts = _load("podcasts")


def fixture(name):
    """Return a document that was recorded from DR's radio API."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def epoch(time_of_day, day="2026-11-05"):
    """Return the epoch seconds of a UTC time, by default on the day of the captured fixtures."""
    return datetime.fromisoformat(f"{day}T{time_of_day}+00:00").timestamp()


class Clock:
    """A clock that only moves when the test says so."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class FakeApi:
    """Stands in for DR's radio API: serves the documents it was given by url."""

    def __init__(self, documents=None):
        self.documents = dict(documents or {})
        self.errors = {}
        self.requests = []

    async def __call__(self, url):
        self.requests.append(url)
        await asyncio.sleep(0)
        if url in self.errors:
            raise self.errors[url]
        if url not in self.documents:
            raise api.NotFoundError(f"DR has nothing at {url}.")
        return self.documents[url]

    def fail(self, *urls):
        """Make exactly the given urls unreachable; without urls, every url is reachable again."""
        self.errors = {url: api.ApiError("Could not load the document") for url in urls}
