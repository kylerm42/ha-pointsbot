"""Integration tests for Phase 1b: People Sync, Scheduling, Entities."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime

import pytest

from custom_components.pointsbot.const import (
    DOMAIN,
    EVENT_WEEKLY_ROLLOVER,
    SIGNAL_POINTSBOT_NEW_PERSON,
    SIGNAL_POINTSBOT_UPDATE,
)
from custom_components.pointsbot.history_log import PointsBotHistoryLog
from custom_components.pointsbot.people_sync import async_sync_people
from custom_components.pointsbot.store import PointsBotStore
from custom_components.pointsbot.weekly_reset import async_perform_weekly_reset

from .conftest import FakeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_person_state(person_id: str, name: str = "Test Person", picture: str | None = None) -> MagicMock:
    """Return a minimal mock state object for a person.* entity."""
    state = MagicMock()
    state.entity_id = person_id
    state.attributes = {"friendly_name": name}
    if picture:
        state.attributes["entity_picture"] = picture
    return state


async def _make_store(hass: MagicMock) -> PointsBotStore:
    """Return a fresh PointsBotStore backed by a FakeStore."""
    with patch("custom_components.pointsbot.store.Store", FakeStore):
        store = PointsBotStore(hass)
    await store.async_load()
    return store


async def _make_history_log(hass: MagicMock) -> PointsBotHistoryLog:
    """Return a fresh PointsBotHistoryLog backed by a FakeStore."""
    with patch("custom_components.pointsbot.history_log.Store", FakeStore):
        log = PointsBotHistoryLog(hass)
    await log.async_load()
    return log


# ---------------------------------------------------------------------------
# people_sync tests
# ---------------------------------------------------------------------------


class TestPeopleSync:
    """Tests for async_sync_people."""

    @pytest.fixture
    def hass(self) -> MagicMock:
        return MagicMock()

    async def test_zero_persons_returns_empty_list(self, hass: MagicMock) -> None:
        """Sync with no person.* entities touches no users."""
        hass.states.async_all.return_value = []
        store = await _make_store(hass)

        result = await async_sync_people(hass, store)

        assert result == []
        assert store.get_all_person_ids() == []

    async def test_single_person_creates_profile(self, hass: MagicMock) -> None:
        """A single person.* entity creates one user profile."""
        hass.states.async_all.return_value = [_make_person_state("person.alice")]
        store = await _make_store(hass)

        result = await async_sync_people(hass, store)

        assert result == ["person.alice"]
        assert store.get_user_data("person.alice") is not None
        assert store.get_user_data("person.alice")["weekly_allotment"] == 0

    async def test_multiple_persons_all_created(self, hass: MagicMock) -> None:
        """Three person.* entities create three profiles."""
        hass.states.async_all.return_value = [
            _make_person_state("person.alice"),
            _make_person_state("person.bob"),
            _make_person_state("person.charlie"),
        ]
        store = await _make_store(hass)

        result = await async_sync_people(hass, store)

        assert set(result) == {"person.alice", "person.bob", "person.charlie"}
        assert len(store.get_all_person_ids()) == 3

    async def test_existing_profile_not_overwritten(self, hass: MagicMock) -> None:
        """An existing user profile is not reset when synced again."""
        hass.states.async_all.return_value = [_make_person_state("person.alice")]
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_set_weekly_allotment("person.alice", 50)

        await async_sync_people(hass, store)

        assert store.get_user_data("person.alice")["weekly_allotment"] == 50

    async def test_disappeared_person_not_deleted(self, hass: MagicMock) -> None:
        """A person removed from HA is never deleted from the store."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")

        # Sync with no persons visible
        hass.states.async_all.return_value = []
        await async_sync_people(hass, store)

        assert store.get_user_data("person.alice") is not None

    async def test_dispatches_new_person_signal(self, hass: MagicMock) -> None:
        """A genuinely new person triggers SIGNAL_POINTSBOT_NEW_PERSON."""
        hass.states.async_all.return_value = [_make_person_state("person.alice")]
        store = await _make_store(hass)
        entry_id = "test_entry_123"

        with patch(
            "custom_components.pointsbot.people_sync.async_dispatcher_send"
        ) as mock_send:
            await async_sync_people(hass, store, entry_id)

        mock_send.assert_called_once_with(
            hass,
            SIGNAL_POINTSBOT_NEW_PERSON.format(entry_id),
            "person.alice",
        )

    async def test_no_signal_for_existing_person(self, hass: MagicMock) -> None:
        """An already-known person does NOT trigger a new-person signal."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        hass.states.async_all.return_value = [_make_person_state("person.alice")]

        with patch(
            "custom_components.pointsbot.people_sync.async_dispatcher_send"
        ) as mock_send:
            await async_sync_people(hass, store, "test_entry")

        mock_send.assert_not_called()

    async def test_no_signal_when_entry_id_is_none(self, hass: MagicMock) -> None:
        """No dispatcher signal is sent when entry_id is not provided."""
        hass.states.async_all.return_value = [_make_person_state("person.alice")]
        store = await _make_store(hass)

        with patch(
            "custom_components.pointsbot.people_sync.async_dispatcher_send"
        ) as mock_send:
            await async_sync_people(hass, store)

        mock_send.assert_not_called()

    async def test_returns_touched_list_including_existing(self, hass: MagicMock) -> None:
        """Return list includes both new and existing persons."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        hass.states.async_all.return_value = [
            _make_person_state("person.alice"),
            _make_person_state("person.bob"),
        ]

        result = await async_sync_people(hass, store, "eid")

        assert set(result) == {"person.alice", "person.bob"}


# ---------------------------------------------------------------------------
# weekly_reset tests
# ---------------------------------------------------------------------------


class TestWeeklyReset:
    """Tests for async_perform_weekly_reset."""

    @pytest.fixture
    def hass(self) -> MagicMock:
        return MagicMock()

    async def test_empty_store_is_noop(self, hass: MagicMock) -> None:
        """Weekly reset with no users completes without error."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)

        await async_perform_weekly_reset(hass, store, log)

        assert log.get_all_events() == []

    async def test_single_user_rollover(self, hass: MagicMock) -> None:
        """A user's weekly_points are moved to total_points."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_adjust_points("person.alice", 15, "bonus work")

        await async_perform_weekly_reset(hass, store, log)

        data = store.get_user_data("person.alice")
        assert data["total_points"] == 15
        assert data["weekly_points"] == 0  # allotment is 0 by default

    async def test_weekly_allotment_applied_on_reset(self, hass: MagicMock) -> None:
        """After rollover, weekly_points equals the user's weekly_allotment."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_set_weekly_allotment("person.alice", 20)

        await async_perform_weekly_reset(hass, store, log)

        assert store.get_user_data("person.alice")["weekly_points"] == 20

    async def test_history_event_appended_per_user(self, hass: MagicMock) -> None:
        """A weekly_rollover history event is recorded for each user."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_upsert_user_profile("person.bob")

        await async_perform_weekly_reset(hass, store, log)

        events = log.get_all_events()
        assert len(events) == 2
        person_ids = {e["person_id"] for e in events}
        assert person_ids == {"person.alice", "person.bob"}
        for ev in events:
            assert ev["event_type"] == EVENT_WEEKLY_ROLLOVER
            assert "id" in ev
            assert "timestamp" in ev
            assert "rolled_over_amount" in ev
            assert "new_allotment" in ev

    async def test_rollover_history_shape(self, hass: MagicMock) -> None:
        """History event contains correct rolled_over_amount and new_allotment."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_set_weekly_allotment("person.alice", 10)
        await store.async_adjust_points("person.alice", 5, "great job")

        await async_perform_weekly_reset(hass, store, log)

        ev = log.get_all_events()[0]
        assert ev["rolled_over_amount"] == 5
        assert ev["new_allotment"] == 10

    async def test_base_tasks_reset(self, hass: MagicMock) -> None:
        """Base task done flags are reset to False after rollover."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        await store.async_toggle_base_task("person.alice", tid)

        await async_perform_weekly_reset(hass, store, log)

        task = store.get_user_data("person.alice")["base_tasks"][0]
        assert task["done"] is False

    async def test_bonus_task_completions_reset(self, hass: MagicMock) -> None:
        """Bonus task completion counts are reset to 0 after rollover."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        tid = await store.async_add_task("person.alice", "bonus", "Extra chore", points_value=5)
        await store.async_complete_bonus_task("person.alice", tid)
        await store.async_complete_bonus_task("person.alice", tid)

        await async_perform_weekly_reset(hass, store, log)

        task = store.get_user_data("person.alice")["bonus_tasks"][0]
        assert task["completions_this_week"] == 0

    async def test_weekly_adjustments_cleared(self, hass: MagicMock) -> None:
        """Per-user weekly_adjustments list is cleared after rollover."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_adjust_points("person.alice", -3, "Left mess")

        await async_perform_weekly_reset(hass, store, log)

        assert store.get_user_data("person.alice")["weekly_adjustments"] == []

    async def test_history_not_cleared_by_rollover(self, hass: MagicMock) -> None:
        """History log retains both adjustment events AND the rollover event."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        event = await store.async_adjust_points("person.alice", -3, "Left mess")
        await log.async_append(event)

        await async_perform_weekly_reset(hass, store, log)

        # Both the manual_adjustment and the weekly_rollover should be present.
        assert len(log.get_all_events()) == 2

    async def test_dispatches_update_signal_with_entry_id(self, hass: MagicMock) -> None:
        """SIGNAL_POINTSBOT_UPDATE is dispatched when entry_id is provided."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)

        with patch(
            "custom_components.pointsbot.weekly_reset.async_dispatcher_send"
        ) as mock_send:
            await async_perform_weekly_reset(hass, store, log, entry_id="eid123")

        mock_send.assert_called_once_with(
            hass, SIGNAL_POINTSBOT_UPDATE.format("eid123")
        )

    async def test_no_signal_without_entry_id(self, hass: MagicMock) -> None:
        """No dispatcher signal when entry_id is None."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)

        with patch(
            "custom_components.pointsbot.weekly_reset.async_dispatcher_send"
        ) as mock_send:
            await async_perform_weekly_reset(hass, store, log)

        mock_send.assert_not_called()

    async def test_multiple_users_all_rolled_over(self, hass: MagicMock) -> None:
        """All registered users are processed in a single reset pass."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        for pid in ["person.alice", "person.bob", "person.charlie"]:
            await store.async_upsert_user_profile(pid)
            await store.async_adjust_points(pid, 10, "good week")

        await async_perform_weekly_reset(hass, store, log)

        for pid in ["person.alice", "person.bob", "person.charlie"]:
            data = store.get_user_data(pid)
            assert data["total_points"] == 10
            assert data["weekly_points"] == 0


# ---------------------------------------------------------------------------
# Sensor entity tests
# ---------------------------------------------------------------------------


class TestPointsBotUserSensor:
    """Tests for PointsBotUserSensor entity logic."""

    @pytest.fixture
    def hass(self) -> MagicMock:
        mock_hass = MagicMock()
        mock_hass.states.get.return_value = None
        return mock_hass

    def _make_sensor(self, hass: MagicMock, store: Any, person_id: str, entry_id: str = "eid") -> Any:
        from custom_components.pointsbot.sensor import PointsBotUserSensor
        sensor = PointsBotUserSensor(store, person_id, entry_id)
        sensor.hass = hass
        return sensor

    async def test_unique_id(self, hass: MagicMock) -> None:
        """unique_id is stable and includes person_id."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        sensor = self._make_sensor(hass, store, "person.alice")
        assert sensor._attr_unique_id == "pointsbot_person.alice"

    async def test_entity_id_slug(self, hass: MagicMock) -> None:
        """entity_id is derived from the part after 'person.'."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.kyle_smith")
        sensor = self._make_sensor(hass, store, "person.kyle_smith")
        assert sensor.entity_id == "sensor.pointsbot_kyle_smith"

    async def test_native_value_is_total_points(self, hass: MagicMock) -> None:
        """native_value returns total_points (lifetime)."""
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_adjust_points("person.alice", 10, "good job")
        await async_perform_weekly_reset(hass, store, log)

        sensor = self._make_sensor(hass, store, "person.alice")
        assert sensor.native_value == 10

    async def test_native_value_unknown_user_returns_zero(self, hass: MagicMock) -> None:
        """If user data is absent (should not happen in practice), return 0."""
        store = await _make_store(hass)
        sensor = self._make_sensor(hass, store, "person.ghost")
        assert sensor.native_value == 0

    async def test_extra_attributes_contain_weekly_data(self, hass: MagicMock) -> None:
        """Attributes include weekly_points, allotment, tasks, adjustments."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        await store.async_set_weekly_allotment("person.alice", 10)
        await store.async_adjust_points("person.alice", 5, "great")

        sensor = self._make_sensor(hass, store, "person.alice")
        attrs = sensor.extra_state_attributes

        assert attrs["weekly_points"] == 5
        assert attrs["weekly_allotment"] == 10
        assert attrs["base_tasks"] == []
        assert attrs["bonus_tasks"] == []
        assert len(attrs["weekly_adjustments"]) == 1
        assert attrs["person_id"] == "person.alice"

    async def test_name_resolved_live_from_person_entity(self, hass: MagicMock) -> None:
        """Name attribute is resolved from the person.* state at read time."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")

        person_state = MagicMock()
        person_state.attributes = {"friendly_name": "Alice", "entity_picture": "/img/alice.jpg"}
        hass.states.get.return_value = person_state

        sensor = self._make_sensor(hass, store, "person.alice")
        attrs = sensor.extra_state_attributes

        assert attrs["name"] == "Alice"
        assert attrs["picture"] == "/img/alice.jpg"

    async def test_name_none_when_person_entity_absent(self, hass: MagicMock) -> None:
        """Name and picture are None when the person.* entity is not found."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        hass.states.get.return_value = None

        sensor = self._make_sensor(hass, store, "person.alice")
        attrs = sensor.extra_state_attributes

        assert attrs["name"] is None
        assert attrs["picture"] is None

    async def test_sensor_name_property_with_person_entity(self, hass: MagicMock) -> None:
        """Sensor name property uses the person's friendly_name."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        person_state = MagicMock()
        person_state.attributes = {"friendly_name": "Alice"}
        hass.states.get.return_value = person_state

        sensor = self._make_sensor(hass, store, "person.alice")
        assert sensor.name == "PointsBot Alice"

    async def test_sensor_name_falls_back_to_person_id(self, hass: MagicMock) -> None:
        """Sensor name falls back to person_id when entity not found."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")
        hass.states.get.return_value = None

        sensor = self._make_sensor(hass, store, "person.alice")
        assert sensor.name == "PointsBot person.alice"

    async def test_update_signal_refreshes_state(self, hass: MagicMock) -> None:
        """_handle_update calls async_write_ha_state."""
        store = await _make_store(hass)
        await store.async_upsert_user_profile("person.alice")

        from custom_components.pointsbot.sensor import PointsBotUserSensor
        sensor = PointsBotUserSensor(store, "person.alice", "eid")
        sensor.hass = hass
        sensor.async_write_ha_state = MagicMock()

        sensor._handle_update()

        sensor.async_write_ha_state.assert_called_once()


# ---------------------------------------------------------------------------
# async_setup_entry orchestration tests
# ---------------------------------------------------------------------------


class TestSetupEntry:
    """Tests for full __init__.async_setup_entry orchestration."""

    def _make_entry(self, entry_id: str = "test_entry_id") -> MagicMock:
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.async_on_unload = MagicMock()
        return entry

    def _make_hass(self, person_states: list | None = None) -> MagicMock:
        hass = MagicMock()
        hass.data = {}
        hass.states.async_all.return_value = person_states or []
        hass.states.get.return_value = None
        hass.config_entries.async_forward_entry_setups = AsyncMock(return_value=True)
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        return hass

    async def test_setup_entry_with_zero_persons(self) -> None:
        """setup_entry succeeds with no person.* entities."""
        hass = self._make_hass()
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            mock_track.return_value = MagicMock()
            from custom_components.pointsbot import async_setup_entry
            result = await async_setup_entry(hass, entry)

        assert result is True
        assert DOMAIN in hass.data
        assert entry.entry_id in hass.data[DOMAIN]
        assert "store" in hass.data[DOMAIN][entry.entry_id]
        assert "history_log" in hass.data[DOMAIN][entry.entry_id]

    async def test_setup_entry_with_one_person(self) -> None:
        """setup_entry creates a user profile for a single person."""
        person_state = _make_person_state("person.alice")
        hass = self._make_hass(person_states=[person_state])
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            mock_track.return_value = MagicMock()
            from custom_components.pointsbot import async_setup_entry
            result = await async_setup_entry(hass, entry)

        store = hass.data[DOMAIN][entry.entry_id]["store"]
        assert store.get_user_data("person.alice") is not None

    async def test_setup_entry_with_multiple_persons(self) -> None:
        """setup_entry creates profiles for all visible persons."""
        hass = self._make_hass(person_states=[
            _make_person_state("person.alice"),
            _make_person_state("person.bob"),
            _make_person_state("person.charlie"),
        ])
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            mock_track.return_value = MagicMock()
            from custom_components.pointsbot import async_setup_entry
            await async_setup_entry(hass, entry)

        store = hass.data[DOMAIN][entry.entry_id]["store"]
        assert len(store.get_all_person_ids()) == 3

    async def test_setup_entry_forwards_to_sensor_platform(self) -> None:
        """setup_entry calls async_forward_entry_setups for SENSOR."""
        from homeassistant.const import Platform
        hass = self._make_hass()
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            mock_track.return_value = MagicMock()
            from custom_components.pointsbot import async_setup_entry
            await async_setup_entry(hass, entry)

        hass.config_entries.async_forward_entry_setups.assert_called_once_with(
            entry, [Platform.SENSOR]
        )

    async def test_setup_entry_registers_time_change_listener(self) -> None:
        """setup_entry registers a daily time-change listener at midnight."""
        hass = self._make_hass()
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            unsub = MagicMock()
            mock_track.return_value = unsub
            from custom_components.pointsbot import async_setup_entry
            await async_setup_entry(hass, entry)

        mock_track.assert_called_once()
        _, kwargs = mock_track.call_args[0], mock_track.call_args[1]
        assert kwargs.get("hour") == 0
        assert kwargs.get("minute") == 0
        assert kwargs.get("second") == 0

    async def test_unload_entry_cancels_listener(self) -> None:
        """async_unload_entry calls the time-change unsub callback."""
        hass = self._make_hass()
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            unsub = MagicMock()
            mock_track.return_value = unsub
            from custom_components.pointsbot import async_setup_entry, async_unload_entry
            await async_setup_entry(hass, entry)
            await async_unload_entry(hass, entry)

        unsub.assert_called_once()

    async def test_unload_entry_unloads_sensor_platform(self) -> None:
        """async_unload_entry calls async_unload_platforms for SENSOR."""
        from homeassistant.const import Platform
        hass = self._make_hass()
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            mock_track.return_value = MagicMock()
            from custom_components.pointsbot import async_setup_entry, async_unload_entry
            await async_setup_entry(hass, entry)
            result = await async_unload_entry(hass, entry)

        assert result is True
        hass.config_entries.async_unload_platforms.assert_called_once_with(
            entry, [Platform.SENSOR]
        )

    async def test_unload_entry_clears_hass_data(self) -> None:
        """async_unload_entry removes the entry from hass.data."""
        hass = self._make_hass()
        entry = self._make_entry()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change") as mock_track,
        ):
            mock_track.return_value = MagicMock()
            from custom_components.pointsbot import async_setup_entry, async_unload_entry
            await async_setup_entry(hass, entry)
            await async_unload_entry(hass, entry)

        assert entry.entry_id not in hass.data.get(DOMAIN, {})

    async def test_monday_guard_skips_non_monday(self) -> None:
        """The time-change callback skips non-Monday weekdays."""
        hass = self._make_hass()
        entry = self._make_entry()
        captured_callback = None

        def _capture_track(h, cb, **kw):
            nonlocal captured_callback
            captured_callback = cb
            return MagicMock()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change", side_effect=_capture_track),
            patch("custom_components.pointsbot.async_perform_weekly_reset", new_callable=AsyncMock) as mock_reset,
        ):
            from custom_components.pointsbot import async_setup_entry
            await async_setup_entry(hass, entry)

            # Tuesday (weekday=1) — must be called inside `with` so patch is active.
            tuesday = datetime(2026, 7, 14, 0, 0, 0)
            await captured_callback(tuesday)
            mock_reset.assert_not_called()

    async def test_monday_guard_fires_on_monday(self) -> None:
        """The time-change callback runs weekly reset on Monday."""
        hass = self._make_hass()
        entry = self._make_entry()
        captured_callback = None

        def _capture_track(h, cb, **kw):
            nonlocal captured_callback
            captured_callback = cb
            return MagicMock()

        with (
            patch("custom_components.pointsbot.store.Store", FakeStore),
            patch("custom_components.pointsbot.history_log.Store", FakeStore),
            patch("custom_components.pointsbot.async_track_time_change", side_effect=_capture_track),
            patch("custom_components.pointsbot.async_perform_weekly_reset", new_callable=AsyncMock) as mock_reset,
        ):
            from custom_components.pointsbot import async_setup_entry
            await async_setup_entry(hass, entry)

            # Monday (weekday=0) — must be called inside `with` so patch is active.
            monday = datetime(2026, 7, 13, 0, 0, 0)
            await captured_callback(monday)
            mock_reset.assert_called_once()
