"""Unit tests for PointsBotHistoryLog (history_log.py)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from .conftest import FakeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_history_log(fake_store: FakeStore):
    from custom_components.pointsbot.history_log import PointsBotHistoryLog

    hass = MagicMock()
    with patch(
        "custom_components.pointsbot.history_log.Store",
        return_value=fake_store,
    ):
        log = PointsBotHistoryLog(hass)
    return log


async def _loaded_log(fake_store: FakeStore, seed: dict | None = None):
    if seed is not None:
        fake_store.seed(seed)
    log = _make_history_log(fake_store)
    await log.async_load()
    return log


# ---------------------------------------------------------------------------
# async_load
# ---------------------------------------------------------------------------


class TestLoad:
    async def test_load_empty_initialises_events_list(self, fake_store):
        log = await _loaded_log(fake_store)
        assert log.get_all_events() == []

    async def test_load_existing_events_preserved(self, fake_store):
        existing_event = {
            "id": "abc",
            "timestamp": "2026-07-10T00:00:00+00:00",
            "event_type": "manual_adjustment",
            "person_id": "person.kid",
            "amount": -5,
            "reason": "Left dishes",
        }
        fake_store.seed({"events": [existing_event]})
        log = _make_history_log(fake_store)
        await log.async_load()
        events = log.get_all_events()
        assert len(events) == 1
        assert events[0]["id"] == "abc"


# ---------------------------------------------------------------------------
# async_append
# ---------------------------------------------------------------------------


class TestAppend:
    async def test_append_assigns_id(self, fake_store):
        log = await _loaded_log(fake_store)
        event_id = await log.async_append({"event_type": "manual_adjustment", "person_id": "person.kid", "amount": 5})
        assert event_id
        events = log.get_all_events()
        assert events[0]["id"] == event_id

    async def test_append_assigns_timestamp(self, fake_store):
        log = await _loaded_log(fake_store)
        await log.async_append({"event_type": "bonus_completion", "person_id": "person.kid"})
        events = log.get_all_events()
        ts = events[0]["timestamp"]
        # Must be a parseable ISO-8601 datetime string
        parsed = datetime.fromisoformat(ts)
        assert parsed is not None

    async def test_append_preserves_caller_fields(self, fake_store):
        log = await _loaded_log(fake_store)
        payload = {
            "event_type": "manual_adjustment",
            "person_id": "person.kid",
            "amount": -5,
            "reason": "Left dishes",
        }
        await log.async_append(dict(payload))
        events = log.get_all_events()
        event = events[0]
        for key, val in payload.items():
            assert event[key] == val

    async def test_append_does_not_overwrite_caller_id(self, fake_store):
        """If caller supplies 'id', the auto-assigned id should take precedence
        (id and timestamp are injected first via **event spreading)."""
        log = await _loaded_log(fake_store)
        # The implementation does {id, timestamp, **event} so caller's id wins.
        # This test documents the current contract — caller id overrides auto.
        # If the implementation changes to always override caller, update test.
        caller_id = "caller-supplied-id"
        await log.async_append({"id": caller_id, "event_type": "test"})
        events = log.get_all_events()
        # With current implementation: auto id set first, then **event spreads
        # caller id on top => caller id wins
        assert events[0]["id"] == caller_id

    async def test_append_is_uncapped(self, fake_store):
        """History must never trim entries — growth is by design."""
        log = await _loaded_log(fake_store)
        for i in range(200):
            await log.async_append({"event_type": "bonus_completion", "index": i})
        assert len(log.get_all_events()) == 200

    async def test_append_persists_to_store(self, fake_store):
        log = await _loaded_log(fake_store)
        await log.async_append({"event_type": "weekly_rollover"})
        # FakeStore should have been saved at least once
        assert fake_store.save_count >= 1

    async def test_append_multiple_events_ordered_oldest_first(self, fake_store):
        log = await _loaded_log(fake_store)
        await log.async_append({"event_type": "manual_adjustment", "seq": 1})
        await log.async_append({"event_type": "bonus_completion", "seq": 2})
        events = log.get_all_events()
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2

    async def test_append_returns_string_uuid(self, fake_store):
        log = await _loaded_log(fake_store)
        event_id = await log.async_append({"event_type": "test"})
        assert isinstance(event_id, str)
        assert len(event_id) == 36  # standard UUID4 hyphenated length

    async def test_append_ids_are_unique(self, fake_store):
        log = await _loaded_log(fake_store)
        ids = [await log.async_append({"event_type": "test"}) for _ in range(10)]
        assert len(set(ids)) == 10
