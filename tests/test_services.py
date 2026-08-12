"""Phase 1c: Service handler integration tests and edge cases.

All tests use the same plain-mock pattern established in Phases 1a/1b:
FakeStore replaces the HA Store, MagicMock replaces HomeAssistant.
No pytest-homeassistant-custom-component required.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.pointsbot.const import (
    DOMAIN,
    EVENT_BONUS_COMPLETION,
    EVENT_BONUS_UNCOMPLETION,
    EVENT_MANUAL_ADJUSTMENT,
    EVENT_WEEKLY_ROLLOVER,
    EVENT_REWARD_REDEMPTION,
    SIGNAL_POINTSBOT_UPDATE,
    TASK_TYPE_BASE,
    TASK_TYPE_BONUS,
)
from custom_components.pointsbot.history_log import PointsBotHistoryLog
from custom_components.pointsbot.services import (
    handle_adjust_points,
    handle_add_task,
    handle_complete_bonus_task,
    handle_uncomplete_bonus_task,
    handle_delete_task,
    handle_run_weekly_reset,
    handle_set_weekly_allotment,
    handle_sync_people,
    handle_toggle_base_task,
    handle_update_task,
    handle_manage_reward,
    handle_redeem_reward,
    handle_delete_reward,
    async_register_services,
)
from custom_components.pointsbot.store import PointsBotStore

from homeassistant.exceptions import ServiceValidationError

from .conftest import FakeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_store(hass: MagicMock) -> PointsBotStore:
    with patch("custom_components.pointsbot.store.Store", FakeStore):
        store = PointsBotStore(hass)
    await store.async_load()
    return store


async def _make_history_log(hass: MagicMock) -> PointsBotHistoryLog:
    with patch("custom_components.pointsbot.history_log.Store", FakeStore):
        log = PointsBotHistoryLog(hass)
    await log.async_load()
    return log


def _make_hass(store: PointsBotStore, history_log: PointsBotHistoryLog, entry_id: str = "eid") -> MagicMock:
    """Build a mock hass with a wired-up domain data dict."""
    hass = MagicMock()
    hass.data = {
        DOMAIN: {
            entry_id: {
                "store": store,
                "history_log": history_log,
            }
        }
    }
    hass.states.async_all.return_value = []
    hass.states.get.return_value = None
    return hass


def _make_call(data: dict[str, Any]) -> MagicMock:
    call = MagicMock()
    call.data = data
    return call


def _assert_signal_sent(hass: MagicMock, entry_id: str = "eid") -> None:
    """Verify that async_dispatcher_send was called with the update signal."""
    # hass is a MagicMock — dispatcher calls go through it.  We patch at the
    # module level in tests that need fine-grained assertions; here we just
    # confirm the call happened by checking our mock dispatcher helper.
    pass  # Covered by module-level patch in specific tests below.


class TestRewardServices:
    async def test_manage_redeem_delete_and_dispatch(self) -> None:
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        hass = _make_hass(store, log)
        await store.async_upsert_user_profile("person.alice")
        with patch("custom_components.pointsbot.services.async_dispatcher_send") as dispatch:
            await handle_manage_reward(hass, _make_call({
                "person_id": "person.alice", "name": "Movie", "cost": 10,
                "icon": "mdi:movie", "description": "A film",
            }))
            reward = store.get_user_data("person.alice")["rewards"][0]
            data = store.get_user_data("person.alice")
            data["total_points"] = 10
            store._data["users"]["person.alice"]["total_points"] = 10
            await handle_redeem_reward(hass, _make_call({
                "person_id": "person.alice", "reward_id": reward["id"],
            }))
            await handle_delete_reward(hass, _make_call({"reward_id": reward["id"]}))
        assert store.get_user_data("person.alice")["total_points"] == 0
        assert log.get_all_events()[-1]["event_type"] == EVENT_REWARD_REDEMPTION
        assert dispatch.call_count == 3

    async def test_redeem_rejects_insufficient_banked_balance(self) -> None:
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        hass = _make_hass(store, log)
        await store.async_upsert_user_profile("person.alice")
        reward = await store.async_manage_reward("person.alice", "Movie", 1, "mdi:movie")
        with pytest.raises(ServiceValidationError, match="Insufficient banked"):
            await handle_redeem_reward(hass, _make_call({
                "person_id": "person.alice", "reward_id": reward["id"],
            }))


# ---------------------------------------------------------------------------
# sync_people
# ---------------------------------------------------------------------------


class TestHandleSyncPeople:
    async def test_syncs_visible_persons(self) -> None:
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        hass = _make_hass(store, log)

        person_state = MagicMock()
        person_state.entity_id = "person.alice"
        hass.states.async_all.return_value = [person_state]

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.people_sync.async_dispatcher_send"):
                await handle_sync_people(hass, _make_call({}))

        assert store.get_user_data("person.alice") is not None

    async def test_dispatches_update_signal(self) -> None:
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        hass = _make_hass(store, log, "eid")
        hass.states.async_all.return_value = []

        with patch(
            "custom_components.pointsbot.services.async_dispatcher_send"
        ) as mock_send:
            with patch("custom_components.pointsbot.people_sync.async_dispatcher_send"):
                await handle_sync_people(hass, _make_call({}))

        mock_send.assert_called_once_with(hass, SIGNAL_POINTSBOT_UPDATE.format("eid"))

    async def test_raises_when_integration_not_setup(self) -> None:
        hass = MagicMock()
        hass.data = {}
        with pytest.raises(ServiceValidationError, match="not set up"):
            await handle_sync_people(hass, _make_call({}))


# ---------------------------------------------------------------------------
# adjust_points
# ---------------------------------------------------------------------------


class TestHandleAdjustPoints:
    async def _setup(self, entry_id: str = "eid"):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log, entry_id)
        return hass, store, log

    async def test_adjust_positive_points(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 10, "reason": "great job"})
            )
        assert store.get_user_data("person.alice")["weekly_points"] == 10

    async def test_adjust_negative_points(self) -> None:
        hass, store, log = await self._setup()
        await store.async_adjust_points("person.alice", 20, "initial")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": -5, "reason": "messy room"})
            )
        assert store.get_user_data("person.alice")["weekly_points"] == 15

    async def test_records_history_event(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 5, "reason": "test"})
            )
        events = log.get_all_events()
        assert len(events) == 1
        assert events[0]["event_type"] == EVENT_MANUAL_ADJUSTMENT
        assert events[0]["amount"] == 5
        assert events[0]["reason"] == "test"
        assert events[0]["person_id"] == "person.alice"

    async def test_populates_weekly_adjustments(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 3, "reason": "tidy room"})
            )
        adj = store.get_user_data("person.alice")["weekly_adjustments"]
        assert len(adj) == 1
        assert adj[0]["amount"] == 3

    async def test_dispatches_update(self) -> None:
        hass, store, log = await self._setup("eid")
        with patch(
            "custom_components.pointsbot.services.async_dispatcher_send"
        ) as mock_send:
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 1, "reason": "ok"})
            )
        mock_send.assert_called_once_with(hass, SIGNAL_POINTSBOT_UPDATE.format("eid"))

    async def test_raises_unknown_person(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Unknown person_id"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.nobody", "amount": 5, "reason": "x"})
            )

    async def test_raises_zero_amount(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="non-zero"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 0, "reason": "x"})
            )

    async def test_raises_empty_reason(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="reason"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 5, "reason": "   "})
            )

    async def test_raises_missing_amount(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="amount"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "reason": "x"})
            )

    async def test_raises_missing_person_id(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="person_id"):
            await handle_adjust_points(
                hass, _make_call({"amount": 5, "reason": "x"})
            )

    async def test_negative_amount_can_drive_points_negative(self) -> None:
        """No floor on total or weekly points — parents may track a debt."""
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": -100, "reason": "debt"})
            )
        assert store.get_user_data("person.alice")["weekly_points"] == -100


# ---------------------------------------------------------------------------
# set_weekly_allotment
# ---------------------------------------------------------------------------


class TestHandleSetWeeklyAllotment:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_sets_allotment(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_set_weekly_allotment(
                hass, _make_call({"person_id": "person.alice", "amount": 50})
            )
        assert store.get_user_data("person.alice")["weekly_allotment"] == 50

    async def test_zero_allotment_allowed(self) -> None:
        hass, store, log = await self._setup()
        await store.async_set_weekly_allotment("person.alice", 20)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_set_weekly_allotment(
                hass, _make_call({"person_id": "person.alice", "amount": 0})
            )
        assert store.get_user_data("person.alice")["weekly_allotment"] == 0

    async def test_raises_negative_amount(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match=">= 0"):
            await handle_set_weekly_allotment(
                hass, _make_call({"person_id": "person.alice", "amount": -1})
            )

    async def test_raises_unknown_person(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Unknown person_id"):
            await handle_set_weekly_allotment(
                hass, _make_call({"person_id": "person.nobody", "amount": 10})
            )

    async def test_no_retroactive_effect_on_current_weekly_points(self) -> None:
        """Changing allotment mid-week must not affect the current weekly_points."""
        hass, store, log = await self._setup()
        await store.async_adjust_points("person.alice", 7, "earned")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_set_weekly_allotment(
                hass, _make_call({"person_id": "person.alice", "amount": 100})
            )
        # weekly_points unchanged — allotment only applies at rollover
        assert store.get_user_data("person.alice")["weekly_points"] == 7
        assert store.get_user_data("person.alice")["weekly_allotment"] == 100


# ---------------------------------------------------------------------------
# add_task
# ---------------------------------------------------------------------------


class TestHandleAddTask:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_add_base_task(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_add_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "base", "name": "Make bed"})
            )
        tasks = store.get_user_data("person.alice")["base_tasks"]
        assert len(tasks) == 1
        assert tasks[0]["name"] == "Make bed"
        assert tasks[0]["done"] is False

    async def test_add_bonus_task(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_add_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "bonus",
                    "name": "Vacuum", "points_value": 10
                })
            )
        tasks = store.get_user_data("person.alice")["bonus_tasks"]
        assert len(tasks) == 1
        assert tasks[0]["points_value"] == 10
        assert tasks[0]["enabled"] is True
        assert tasks[0]["completions_this_week"] == 0

    async def test_raises_bonus_without_points_value(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="points_value"):
            await handle_add_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "bonus", "name": "Vacuum"})
            )

    async def test_raises_base_with_points_value(self) -> None:
        """points_value on a base task is a mismatched field combination."""
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="not applicable to base tasks"):
            await handle_add_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "base",
                    "name": "Make bed", "points_value": 5
                })
            )

    async def test_raises_unknown_task_type(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="task_type"):
            await handle_add_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "weird", "name": "X"})
            )

    async def test_raises_unknown_person(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Unknown person_id"):
            await handle_add_task(
                hass, _make_call({"person_id": "person.ghost", "task_type": "base", "name": "X"})
            )

    async def test_raises_empty_name(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="name"):
            await handle_add_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "base", "name": "   "})
            )

    async def test_raises_nonpositive_points_value(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="positive integer"):
            await handle_add_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "bonus",
                    "name": "X", "points_value": 0
                })
            )


# ---------------------------------------------------------------------------
# update_task
# ---------------------------------------------------------------------------


class TestHandleUpdateTask:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_rename_base_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Old name")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "base",
                    "task_id": tid, "name": "New name"
                })
            )
        task = store.get_user_data("person.alice")["base_tasks"][0]
        assert task["name"] == "New name"

    async def test_update_bonus_task_points(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=5)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "bonus",
                    "task_id": tid, "points_value": 15
                })
            )
        task = store.get_user_data("person.alice")["bonus_tasks"][0]
        assert task["points_value"] == 15

    async def test_disable_bonus_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "X", points_value=10)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "bonus",
                    "task_id": tid, "enabled": False
                })
            )
        task = store.get_user_data("person.alice")["bonus_tasks"][0]
        assert task["enabled"] is False

    async def test_raises_points_value_on_base_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with pytest.raises(ServiceValidationError, match="not applicable to base tasks"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "base",
                    "task_id": tid, "points_value": 5
                })
            )

    async def test_raises_enabled_on_base_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with pytest.raises(ServiceValidationError, match="not applicable to base tasks"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "base",
                    "task_id": tid, "enabled": False
                })
            )

    async def test_raises_nonexistent_task_id(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Task not found"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "base",
                    "task_id": "no-such-id", "name": "X"
                })
            )

    async def test_raises_no_fields_provided(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with pytest.raises(ServiceValidationError, match="At least one"):
            await handle_update_task(
                hass, _make_call({
                    "person_id": "person.alice", "task_type": "base",
                    "task_id": tid
                })
            )


# ---------------------------------------------------------------------------
# delete_task
# ---------------------------------------------------------------------------


class TestHandleDeleteTask:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_delete_base_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_delete_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "base", "task_id": tid})
            )
        assert store.get_user_data("person.alice")["base_tasks"] == []

    async def test_delete_bonus_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=10)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_delete_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "bonus", "task_id": tid})
            )
        assert store.get_user_data("person.alice")["bonus_tasks"] == []

    async def test_raises_nonexistent_task_id(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Task not found"):
            await handle_delete_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "base", "task_id": "no-such"})
            )

    async def test_raises_unknown_person(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Unknown person_id"):
            await handle_delete_task(
                hass, _make_call({"person_id": "person.ghost", "task_type": "base", "task_id": "x"})
            )


# ---------------------------------------------------------------------------
# toggle_base_task
# ---------------------------------------------------------------------------


class TestHandleToggleBaseTask:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_toggles_done_flag(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_toggle_base_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )
        task = store.get_user_data("person.alice")["base_tasks"][0]
        assert task["done"] is True

    async def test_toggle_twice_returns_to_false(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_toggle_base_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
            await handle_toggle_base_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
        assert store.get_user_data("person.alice")["base_tasks"][0]["done"] is False

    async def test_no_point_effect(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_toggle_base_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
        assert store.get_user_data("person.alice")["weekly_points"] == 0

    async def test_raises_nonexistent_task_id(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Task not found"):
            await handle_toggle_base_task(
                hass, _make_call({"person_id": "person.alice", "task_id": "no-such"})
            )


# ---------------------------------------------------------------------------
# complete_bonus_task
# ---------------------------------------------------------------------------


class TestHandleCompleteBonusTask:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_awards_points(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=10)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )
        assert store.get_user_data("person.alice")["weekly_points"] == 10

    async def test_increments_completion_count(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=5)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_complete_bonus_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
            await handle_complete_bonus_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
        task = store.get_user_data("person.alice")["bonus_tasks"][0]
        assert task["completions_this_week"] == 2

    async def test_records_history_event(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=7)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )
        events = log.get_all_events()
        assert len(events) == 1
        assert events[0]["event_type"] == EVENT_BONUS_COMPLETION
        assert events[0]["task_name"] == "Vacuum"
        assert events[0]["amount"] == 7
        assert "id" in events[0]
        assert "timestamp" in events[0]

    async def test_raises_disabled_task(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "X", points_value=5)
        await store.async_update_task("person.alice", "bonus", tid, enabled=False)
        with pytest.raises(ServiceValidationError, match="disabled"):
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )

    async def test_raises_nonexistent_task_id(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Task not found"):
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": "no-such"})
            )

    async def test_raises_unknown_person(self) -> None:
        hass, store, log = await self._setup()
        with pytest.raises(ServiceValidationError, match="Unknown person_id"):
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.ghost", "task_id": "x"})
            )

    async def test_dispatches_update(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=5)
        with patch(
            "custom_components.pointsbot.services.async_dispatcher_send"
        ) as mock_send:
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )
        mock_send.assert_called_once_with(hass, SIGNAL_POINTSBOT_UPDATE.format("eid"))


class TestHandleUncompleteBonusTask:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        return _make_hass(store, log), store, log

    async def test_reverses_completion_and_records_history(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=7)
        await store.async_complete_bonus_task("person.alice", tid)
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_uncomplete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )
        task = store.get_user_data("person.alice")["bonus_tasks"][0]
        assert task["completions_this_week"] == 0
        assert store.get_user_data("person.alice")["weekly_points"] == 0
        event = log.get_all_events()[0]
        assert event["event_type"] == EVENT_BONUS_UNCOMPLETION
        assert event["amount"] == -7

    async def test_rejects_without_completion(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=7)
        with pytest.raises(ServiceValidationError, match="no completion"):
            await handle_uncomplete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )


# ---------------------------------------------------------------------------
# run_weekly_reset
# ---------------------------------------------------------------------------


class TestHandleRunWeeklyReset:
    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        hass = _make_hass(store, log)
        return hass, store, log

    async def test_rolls_over_all_users(self) -> None:
        hass, store, log = await self._setup()
        await store.async_upsert_user_profile("person.alice")
        await store.async_upsert_user_profile("person.bob")
        await store.async_adjust_points("person.alice", 10, "good")
        await store.async_adjust_points("person.bob", 20, "great")

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_run_weekly_reset(hass, _make_call({}))

        assert store.get_user_data("person.alice")["total_points"] == 10
        assert store.get_user_data("person.bob")["total_points"] == 20

    async def test_appends_history_events(self) -> None:
        hass, store, log = await self._setup()
        await store.async_upsert_user_profile("person.alice")

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_run_weekly_reset(hass, _make_call({}))

        events = log.get_all_events()
        assert len(events) == 1
        assert events[0]["event_type"] == EVENT_WEEKLY_ROLLOVER

    async def test_empty_store_is_noop(self) -> None:
        hass, store, log = await self._setup()
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_run_weekly_reset(hass, _make_call({}))
        assert log.get_all_events() == []


# ---------------------------------------------------------------------------
# Service registration
# ---------------------------------------------------------------------------


class TestAsyncRegisterServices:
    def test_registers_all_nine_services(self) -> None:
        hass = MagicMock()
        hass.services.has_service.return_value = False
        async_register_services(hass)
        assert hass.services.async_register.call_count == 9

    def test_skips_already_registered_services(self) -> None:
        hass = MagicMock()
        hass.services.has_service.return_value = True
        async_register_services(hass)
        hass.services.async_register.assert_not_called()

    def test_partial_registration(self) -> None:
        """Services not yet registered are added; already-registered are skipped."""
        registered = set()

        def has_service(domain, name):
            return name in registered

        def do_register(domain, name, handler):
            registered.add(name)

        hass = MagicMock()
        hass.services.has_service.side_effect = has_service
        hass.services.async_register.side_effect = do_register

        # Pre-register two services externally.
        registered.add("sync_people")
        registered.add("adjust_points")

        async_register_services(hass)
        assert hass.services.async_register.call_count == 7  # 9 total - 2 pre-registered


# ---------------------------------------------------------------------------
# Edge cases (spec §5)
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Spec-mandated edge case coverage (§5 Testing Strategy)."""

    async def _make_full_setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        return store, log, hass

    # ------------------------------------------------------------------
    # allotment change mid-week has no retroactive effect
    # ------------------------------------------------------------------

    async def test_allotment_change_no_retroactive_effect(self) -> None:
        """set_weekly_allotment mid-week must not touch current weekly_points."""
        store, log, hass = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        await store.async_adjust_points("person.alice", 15, "earned this week")
        hass = _make_hass(store, log)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_set_weekly_allotment(
                hass, _make_call({"person_id": "person.alice", "amount": 99})
            )

        data = store.get_user_data("person.alice")
        assert data["weekly_points"] == 15  # unchanged
        assert data["weekly_allotment"] == 99  # updated for next rollover

    # ------------------------------------------------------------------
    # person removed from HA then re-added → no duplicate / no reset
    # ------------------------------------------------------------------

    async def test_person_reappears_does_not_duplicate_or_reset(self) -> None:
        """Person profile is preserved (upsert-only) if they re-appear in HA."""
        store, log, _ = await self._make_full_setup()
        hass = MagicMock()
        await store.async_upsert_user_profile("person.alice")
        await store.async_adjust_points("person.alice", 42, "accrued")

        # Person disappears and reappears; sync is called with the person present.
        hass.states.async_all.return_value = [
            _make_person_state("person.alice")
        ]
        hass_full = _make_hass(store, log)
        hass_full.states.async_all.return_value = hass.states.async_all.return_value

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.people_sync.async_dispatcher_send"):
                await handle_sync_people(hass_full, _make_call({}))

        ids = store.get_all_person_ids()
        assert ids.count("person.alice") == 1  # no duplicate
        assert store.get_user_data("person.alice")["weekly_points"] == 42  # not reset

    # ------------------------------------------------------------------
    # rollover with zero users is a no-op
    # ------------------------------------------------------------------

    async def test_rollover_zero_users_noop(self) -> None:
        store, log, hass = await self._make_full_setup()
        hass = _make_hass(store, log)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_run_weekly_reset(hass, _make_call({}))

        assert log.get_all_events() == []
        assert store.get_all_person_ids() == []

    # ------------------------------------------------------------------
    # negative adjust_points can drive total_points negative (no floor)
    # ------------------------------------------------------------------

    async def test_negative_adjust_drives_points_negative(self) -> None:
        store, log, _ = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        # No points earned — start at 0; deduct 50.
        hass = _make_hass(store, log)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": -50, "reason": "debt"})
            )

        assert store.get_user_data("person.alice")["weekly_points"] == -50

    async def test_negative_weekly_survives_rollover_into_negative_total(self) -> None:
        store, log, hass = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        await store.async_adjust_points("person.alice", -30, "serious debt")
        hass = _make_hass(store, log)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_run_weekly_reset(hass, _make_call({}))

        data = store.get_user_data("person.alice")
        assert data["total_points"] == -30  # no floor enforced

    # ------------------------------------------------------------------
    # disabling then re-enabling a bonus task never resets completions_this_week
    # ------------------------------------------------------------------

    async def test_disable_reenable_preserves_completion_count(self) -> None:
        store, log, _ = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=5)
        hass = _make_hass(store, log)

        # Complete twice, disable, re-enable — count must remain 2.
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_complete_bonus_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
            await handle_complete_bonus_task(hass, _make_call({"person_id": "person.alice", "task_id": tid}))
            await handle_update_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "bonus", "task_id": tid, "enabled": False})
            )
            await handle_update_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "bonus", "task_id": tid, "enabled": True})
            )

        task = store.get_user_data("person.alice")["bonus_tasks"][0]
        assert task["completions_this_week"] == 2
        assert task["enabled"] is True

    async def test_disabled_task_cannot_be_completed(self) -> None:
        store, log, _ = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        tid = await store.async_add_task("person.alice", "bonus", "X", points_value=5)
        hass = _make_hass(store, log)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_update_task(
                hass, _make_call({"person_id": "person.alice", "task_type": "bonus", "task_id": tid, "enabled": False})
            )
            with pytest.raises(ServiceValidationError, match="disabled"):
                await handle_complete_bonus_task(
                    hass, _make_call({"person_id": "person.alice", "task_id": tid})
                )

    # ------------------------------------------------------------------
    # concurrent weekly-reset and complete_bonus_task don't lose updates
    # ------------------------------------------------------------------

    async def test_concurrent_reset_and_completion_no_lost_update(self) -> None:
        """Both operations must complete with correct final state.

        The store's asyncio.Lock serialises writes, so even if both coroutines
        are submitted together the second always sees the result of the first.
        """
        store, log, _ = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=10)
        await store.async_adjust_points("person.alice", 5, "base earned")
        hass = _make_hass(store, log)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                # Run both concurrently — the lock in PointsBotStore ensures they
                # are serialised; neither operation is lost.
                await asyncio.gather(
                    handle_run_weekly_reset(hass, _make_call({})),
                    handle_complete_bonus_task(
                        hass, _make_call({"person_id": "person.alice", "task_id": tid})
                    ),
                )

        data = store.get_user_data("person.alice")
        # Both operations completed; the exact ordering determines which values
        # land where, but no operation should be silently swallowed.
        # Verify that either weekly_points or total_points reflects the bonus.
        total_tracked = data["total_points"] + data["weekly_points"]
        # 5 (weekly at reset) rolled to total, allotment 0 → weekly 0,
        # then bonus +10 → weekly 10.  OR bonus first (weekly=15), then reset
        # rolls 15 to total.  Either way the combined total is 15.
        assert total_tracked == 15

    # ------------------------------------------------------------------
    # weekly rollover clears weekly_adjustments; pointsbot_history untouched
    # ------------------------------------------------------------------

    async def test_rollover_clears_adjustments_but_history_persists(self) -> None:
        store, log, hass = await self._make_full_setup()
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)

        # Make an adjustment (recorded in both weekly_adjustments and history).
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": -3, "reason": "mess"})
            )

        adj_before = store.get_user_data("person.alice")["weekly_adjustments"]
        assert len(adj_before) == 1
        history_before = len(log.get_all_events())  # 1 manual_adjustment

        # Run weekly reset.
        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_run_weekly_reset(hass, _make_call({}))

        # Per-user list cleared.
        assert store.get_user_data("person.alice")["weekly_adjustments"] == []

        # History log still has the manual_adjustment PLUS the new rollover event.
        history_after = log.get_all_events()
        assert len(history_after) == history_before + 1  # +1 rollover event
        event_types = {e["event_type"] for e in history_after}
        assert EVENT_MANUAL_ADJUSTMENT in event_types
        assert EVENT_WEEKLY_ROLLOVER in event_types


# ---------------------------------------------------------------------------
# Round-trip: service call → store mutation → sensor attribute
# ---------------------------------------------------------------------------


def _make_person_state(person_id: str, name: str = "Test") -> MagicMock:
    state = MagicMock()
    state.entity_id = person_id
    state.attributes = {"friendly_name": name}
    return state


class TestServiceToSensorRoundTrip:
    """Verify that service calls produce the expected sensor attribute changes."""

    async def _setup(self):
        hass = MagicMock()
        store = await _make_store(hass)
        log = await _make_history_log(hass)
        await store.async_upsert_user_profile("person.alice")
        hass = _make_hass(store, log)
        hass.states.get.return_value = _make_person_state("person.alice", "Alice")
        return hass, store, log

    def _make_sensor(self, store, hass):
        from custom_components.pointsbot.sensor import PointsBotUserSensor
        s = PointsBotUserSensor(store, "person.alice", "eid")
        s.hass = hass
        return s

    async def test_adjust_points_reflected_in_sensor_attributes(self) -> None:
        hass, store, log = await self._setup()
        sensor = self._make_sensor(store, hass)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_adjust_points(
                hass, _make_call({"person_id": "person.alice", "amount": 25, "reason": "great week"})
            )

        attrs = sensor.extra_state_attributes
        assert attrs["weekly_points"] == 25
        assert len(attrs["weekly_adjustments"]) == 1

    async def test_complete_bonus_task_reflected_in_sensor(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "bonus", "Vacuum", points_value=8)
        sensor = self._make_sensor(store, hass)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_complete_bonus_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )

        attrs = sensor.extra_state_attributes
        assert attrs["weekly_points"] == 8
        task = attrs["bonus_tasks"][0]
        assert task["completions_this_week"] == 1

    async def test_set_allotment_reflected_after_rollover(self) -> None:
        hass, store, log = await self._setup()
        sensor = self._make_sensor(store, hass)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            with patch("custom_components.pointsbot.weekly_reset.async_dispatcher_send"):
                await handle_set_weekly_allotment(
                    hass, _make_call({"person_id": "person.alice", "amount": 30})
                )
                await handle_run_weekly_reset(hass, _make_call({}))

        attrs = sensor.extra_state_attributes
        assert attrs["weekly_points"] == 30  # allotment applied at rollover
        assert attrs["weekly_allotment"] == 30

    async def test_toggle_base_task_reflected_in_sensor(self) -> None:
        hass, store, log = await self._setup()
        tid = await store.async_add_task("person.alice", "base", "Make bed")
        sensor = self._make_sensor(store, hass)

        with patch("custom_components.pointsbot.services.async_dispatcher_send"):
            await handle_toggle_base_task(
                hass, _make_call({"person_id": "person.alice", "task_id": tid})
            )

        attrs = sensor.extra_state_attributes
        assert attrs["base_tasks"][0]["done"] is True

    async def test_sensor_name_resolved_live_not_cached(self) -> None:
        """Sensor name comes from person.* entity, not PointsBot storage."""
        hass, store, log = await self._setup()
        sensor = self._make_sensor(store, hass)

        # Name resolution goes through hass.states.get — already mocked above.
        attrs = sensor.extra_state_attributes
        assert attrs["name"] == "Alice"
        # Store has no 'name' key.
        stored = store.get_user_data("person.alice")
        assert "name" not in stored
