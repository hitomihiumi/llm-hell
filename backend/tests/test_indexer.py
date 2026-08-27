"""The background refresh of the semantic index.

An index nobody refreshes is worse than no index: it answers confidently from
a corpus that has moved on, and a stale answer carries nothing that says so.
So the crawl runs on a timer - and the properties worth pinning are the ones
that decide whether a timer is safe to leave running.
"""

import asyncio

import pytest

from app.core.config import Settings
from app.services.search import indexer


def settings(**overrides) -> Settings:
    return Settings(
        **{"semantic_enabled": True, "semantic_index_interval_minutes": 30, **overrides}
    )


# --- whether it runs at all ---------------------------------------------------


def test_it_does_not_run_when_the_semantic_source_is_off():
    """A deployment not using the index should not pay for a crawl of every
    source it has."""
    assert indexer.start(settings(semantic_enabled=False)) is None


def test_an_interval_of_zero_turns_it_off():
    """Leaving `manage.py index-corpus` as the only way to fill the index is a
    legitimate choice, not a misconfiguration."""
    assert indexer.start(settings(semantic_index_interval_minutes=0)) is None


@pytest.mark.asyncio
async def test_it_starts_a_task_when_enabled():
    task = indexer.start(settings())
    try:
        assert task is not None
        assert not task.done()
    finally:
        await indexer.stop(task)


# --- the loop -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_failing_pass_does_not_end_the_loop(monkeypatch):
    """A source down at 09:00 is usually up at 09:30. A crawl that died on the
    first refused connection would freeze the index at whatever it held when
    the network last hiccuped."""
    calls = []

    async def exploding(_settings):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("gitlab is down")

    monkeypatch.setattr(indexer, "run_once", exploding)
    monkeypatch.setattr(indexer, "FIRST_PASS_DELAY_SECONDS", 0.0)

    task = asyncio.create_task(indexer._loop(settings(semantic_index_interval_minutes=1)))
    # Two passes' worth of scheduling, without waiting a real minute: the
    # sleep between passes is what the timeout interrupts.
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await indexer.stop(task)

    assert calls, "the loop should have attempted at least one pass"


@pytest.mark.asyncio
async def test_stopping_waits_for_the_task(monkeypatch):
    """Shutdown must not race a crawl that is halfway through writing rows."""
    started = asyncio.Event()

    async def slow(_settings):
        started.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(indexer, "run_once", slow)
    monkeypatch.setattr(indexer, "FIRST_PASS_DELAY_SECONDS", 0.0)

    task = asyncio.create_task(indexer._loop(settings()))
    await asyncio.wait_for(started.wait(), timeout=2)

    await indexer.stop(task)

    assert task.done()


@pytest.mark.asyncio
async def test_stopping_nothing_is_not_an_error():
    """`start` returns None when the refresh is off, and the lifespan hands
    that straight back to `stop`."""
    await indexer.stop(None)
