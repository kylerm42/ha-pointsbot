"""Unit tests for PointsBotStore (store.py).

All tests use FakeStore in place of homeassistant.helpers.storage.Store and a
MagicMock in place of HomeAssistant so no HA event loop or hass fixtures are
required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch, MagicMock
import copy

import pytest

from .conftest import FakeStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store(fake_store: FakeStore):
    """Return a PointsBotStore wired to *fake_store*."""
    from custom_components.pointsbot.store import PointsBotStore

    hass = MagicMock()
    with patch(
        "custom_components.pointsbot.store.Store",
        return_value=fake_store,
    ):
        store = PointsBotStore(hass)
    return store


async def _loaded_store(fake_store: FakeStore, seed: dict | None = None):
    """Return an initialised PointsBotStore, optionally pre-seeded."""
    if seed is not None:
        fake_store.seed(seed)
    store = _make_store(fake_store)
    await store.async_load()
    return store


PERSON_ID = "person.kid_one"


# ---------------------------------------------------------------------------
# async_load / async_save
# ---------------------------------------------------------------------------


class TestLoadSave:
    async def test_load_empty_initialises_users_dict(self, fake_store):
        store = await _loaded_store(fake_store)
        assert store.get_all_person_ids() == []

    async def test_load_existing_data_preserved(self, fake_store):
        fake_store.seed({"users": {PERSON_ID: {
            "weekly_allotment": 10,
            "total_points": 5,
            "weekly_points": 3,
            "base_tasks": [],
            "bonus_tasks": [],
            "weekly_adjustments": [],
        }}})
        store = _make_store(fake_store)
        await store.async_load()
        assert PERSON_ID in store.get_all_person_ids()
        user = store.get_user_data(PERSON_ID)
        assert user["total_points"] == 5

    async def test_save_persists_to_fake_store(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        # At least one save must have occurred
        assert fake_store.save_count >= 1


# ---------------------------------------------------------------------------
# async_upsert_user_profile
# ---------------------------------------------------------------------------


class TestUpsertUserProfile:
    async def test_creates_user_with_defaults(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user is not None
        assert user["weekly_allotment"] == 0
        assert user["total_points"] == 0
        assert user["weekly_points"] == 0
        assert user["base_tasks"] == []
        assert user["bonus_tasks"] == []
        assert user["weekly_adjustments"] == []

    async def test_upsert_existing_is_noop(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        await store.async_set_weekly_allotment(PERSON_ID, 25)
        # upsert again must not reset allotment
        await store.async_upsert_user_profile(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_allotment"] == 25

    async def test_no_name_or_picture_stored(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert "name" not in user
        assert "picture" not in user


# ---------------------------------------------------------------------------
# async_add_task
# ---------------------------------------------------------------------------


class TestAddTask:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_add_base_task(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Make bed")
        user = store.get_user_data(PERSON_ID)
        assert len(user["base_tasks"]) == 1
        task = user["base_tasks"][0]
        assert task["id"] == task_id
        assert task["name"] == "Make bed"
        assert task["done"] is False

    async def test_add_bonus_task(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Extra chore", points_value=5)
        user = store.get_user_data(PERSON_ID)
        assert len(user["bonus_tasks"]) == 1
        task = user["bonus_tasks"][0]
        assert task["id"] == task_id
        assert task["name"] == "Extra chore"
        assert task["points_value"] == 5
        assert task["enabled"] is True
        assert task["completions_this_week"] == 0

    async def test_add_bonus_task_without_points_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(ValueError, match="points_value"):
            await store.async_add_task(PERSON_ID, "bonus", "No points")

    async def test_add_task_unknown_type_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(ValueError, match="Unknown task_type"):
            await store.async_add_task(PERSON_ID, "invalid", "Whatever")

    async def test_add_task_unknown_person_raises(self, fake_store):
        store = await _loaded_store(fake_store)
        with pytest.raises(KeyError, match="Unknown person_id"):
            await store.async_add_task("person.ghost", "base", "Haunt")

    async def test_add_task_returns_unique_ids(self, fake_store):
        store = await self._setup(fake_store)
        id1 = await store.async_add_task(PERSON_ID, "base", "Task A")
        id2 = await store.async_add_task(PERSON_ID, "base", "Task B")
        assert id1 != id2


# ---------------------------------------------------------------------------
# async_update_task
# ---------------------------------------------------------------------------


class TestUpdateTask:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_rename_base_task(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Old name")
        await store.async_update_task(PERSON_ID, "base", task_id, name="New name")
        user = store.get_user_data(PERSON_ID)
        assert user["base_tasks"][0]["name"] == "New name"

    async def test_update_bonus_points_and_enabled(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Chore", points_value=5)
        await store.async_update_task(PERSON_ID, "bonus", task_id, points_value=10, enabled=False)
        user = store.get_user_data(PERSON_ID)
        task = user["bonus_tasks"][0]
        assert task["points_value"] == 10
        assert task["enabled"] is False

    async def test_update_base_task_ignores_bonus_fields(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Sweep")
        # points_value and enabled should be silently ignored for base tasks
        await store.async_update_task(PERSON_ID, "base", task_id, points_value=99, enabled=False)
        user = store.get_user_data(PERSON_ID)
        task = user["base_tasks"][0]
        assert "points_value" not in task
        assert "enabled" not in task

    async def test_update_unknown_task_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(KeyError, match="not found"):
            await store.async_update_task(PERSON_ID, "base", "bad-uuid", name="X")


# ---------------------------------------------------------------------------
# async_delete_task
# ---------------------------------------------------------------------------


class TestDeleteTask:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_delete_base_task(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Sweep")
        await store.async_delete_task(PERSON_ID, "base", task_id)
        user = store.get_user_data(PERSON_ID)
        assert user["base_tasks"] == []

    async def test_delete_bonus_task(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Chore", points_value=5)
        await store.async_delete_task(PERSON_ID, "bonus", task_id)
        user = store.get_user_data(PERSON_ID)
        assert user["bonus_tasks"] == []

    async def test_delete_unknown_task_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(KeyError, match="not found"):
            await store.async_delete_task(PERSON_ID, "base", "nonexistent")


# ---------------------------------------------------------------------------
# async_toggle_base_task
# ---------------------------------------------------------------------------


class TestToggleBaseTask:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_toggle_sets_done_true(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Make bed")
        result = await store.async_toggle_base_task(PERSON_ID, task_id)
        assert result is True
        user = store.get_user_data(PERSON_ID)
        assert user["base_tasks"][0]["done"] is True

    async def test_toggle_sets_done_false_on_second_call(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Make bed")
        await store.async_toggle_base_task(PERSON_ID, task_id)
        result = await store.async_toggle_base_task(PERSON_ID, task_id)
        assert result is False

    async def test_toggle_does_not_affect_points(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Make bed")
        await store.async_toggle_base_task(PERSON_ID, task_id)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == 0

    async def test_toggle_unknown_task_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(KeyError):
            await store.async_toggle_base_task(PERSON_ID, "bad-uuid")


# ---------------------------------------------------------------------------
# async_complete_bonus_task
# ---------------------------------------------------------------------------


class TestCompleteBonusTask:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_completion_increments_count_and_points(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        event = await store.async_complete_bonus_task(PERSON_ID, task_id)

        user = store.get_user_data(PERSON_ID)
        assert user["bonus_tasks"][0]["completions_this_week"] == 1
        assert user["weekly_points"] == 5

    async def test_completion_returns_history_event(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        event = await store.async_complete_bonus_task(PERSON_ID, task_id)

        assert event["event_type"] == "bonus_completion"
        assert event["person_id"] == PERSON_ID
        assert event["task_id"] == task_id
        assert event["task_name"] == "Vacuum"
        assert event["amount"] == 5

    async def test_completion_multiple_times_accumulates(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        await store.async_complete_bonus_task(PERSON_ID, task_id)
        await store.async_complete_bonus_task(PERSON_ID, task_id)

        user = store.get_user_data(PERSON_ID)
        assert user["bonus_tasks"][0]["completions_this_week"] == 2
        assert user["weekly_points"] == 10

    async def test_completion_disabled_task_raises(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        await store.async_update_task(PERSON_ID, "bonus", task_id, enabled=False)
        with pytest.raises(ValueError, match="disabled"):
            await store.async_complete_bonus_task(PERSON_ID, task_id)

    async def test_completion_unknown_task_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(KeyError):
            await store.async_complete_bonus_task(PERSON_ID, "bad-uuid")


class TestUncompleteBonusTask:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_uncompletion_decrements_count_and_points(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        await store.async_complete_bonus_task(PERSON_ID, task_id)
        event = await store.async_uncomplete_bonus_task(PERSON_ID, task_id)

        task = store.get_user_data(PERSON_ID)["bonus_tasks"][0]
        assert task["completions_this_week"] == 0
        assert store.get_user_data(PERSON_ID)["weekly_points"] == 0
        assert event["event_type"] == "bonus_uncompletion"
        assert event["amount"] == -5

    async def test_uncompletion_without_completion_raises(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        with pytest.raises(ValueError, match="no completion"):
            await store.async_uncomplete_bonus_task(PERSON_ID, task_id)

    async def test_uncompletion_unknown_task_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(KeyError):
            await store.async_uncomplete_bonus_task(PERSON_ID, "bad-uuid")


# ---------------------------------------------------------------------------
# async_adjust_points
# ---------------------------------------------------------------------------


class TestAdjustPoints:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_positive_adjustment_increases_weekly_points(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 10, "Helping neighbour")
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == 10

    async def test_negative_adjustment_decreases_weekly_points(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, -5, "Left dishes out")
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == -5

    async def test_adjustment_appended_to_weekly_adjustments(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 3, "Good behaviour")
        user = store.get_user_data(PERSON_ID)
        assert len(user["weekly_adjustments"]) == 1
        adj = user["weekly_adjustments"][0]
        assert adj["amount"] == 3
        assert adj["reason"] == "Good behaviour"
        assert "id" in adj
        assert "timestamp" in adj

    async def test_adjustment_returns_history_event(self, fake_store):
        store = await self._setup(fake_store)
        event = await store.async_adjust_points(PERSON_ID, -5, "Left dishes out")
        assert event["event_type"] == "manual_adjustment"
        assert event["person_id"] == PERSON_ID
        assert event["amount"] == -5
        assert event["reason"] == "Left dishes out"

    async def test_empty_reason_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(ValueError, match="reason"):
            await store.async_adjust_points(PERSON_ID, 5, "")

    async def test_whitespace_only_reason_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(ValueError, match="reason"):
            await store.async_adjust_points(PERSON_ID, 5, "   ")

    async def test_zero_amount_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(ValueError, match="non-zero"):
            await store.async_adjust_points(PERSON_ID, 0, "Nothing happened")

    async def test_negative_balance_allowed(self, fake_store):
        """Negative weekly_points is explicitly permitted (no floor enforced)."""
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, -999, "Points debt")
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == -999


# ---------------------------------------------------------------------------
# async_set_weekly_allotment
# ---------------------------------------------------------------------------


class TestSetWeeklyAllotment:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_sets_allotment(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_set_weekly_allotment(PERSON_ID, 20)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_allotment"] == 20

    async def test_zero_allotment_allowed(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_set_weekly_allotment(PERSON_ID, 0)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_allotment"] == 0

    async def test_negative_allotment_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(ValueError, match=">= 0"):
            await store.async_set_weekly_allotment(PERSON_ID, -1)

    async def test_allotment_change_does_not_affect_current_weekly_points(self, fake_store):
        """Allotment change takes effect at rollover, not retroactively."""
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 7, "Earned")
        await store.async_set_weekly_allotment(PERSON_ID, 100)
        user = store.get_user_data(PERSON_ID)
        # weekly_points unchanged by the allotment change
        assert user["weekly_points"] == 7


# ---------------------------------------------------------------------------
# async_apply_weekly_rollover
# ---------------------------------------------------------------------------


class TestWeeklyRollover:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        return store

    async def test_rollover_adds_weekly_to_total(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 12, "Good week")
        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["total_points"] == 12
        assert user["weekly_points"] == 0  # allotment=0

    async def test_rollover_resets_weekly_points_to_allotment(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_set_weekly_allotment(PERSON_ID, 10)
        await store.async_adjust_points(PERSON_ID, 5, "Bonus")
        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == 10  # reset to allotment

    async def test_rollover_resets_base_task_done_flags(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "base", "Make bed")
        await store.async_toggle_base_task(PERSON_ID, task_id)
        # confirm done=True
        user = store.get_user_data(PERSON_ID)
        assert user["base_tasks"][0]["done"] is True

        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["base_tasks"][0]["done"] is False

    async def test_rollover_resets_bonus_completions(self, fake_store):
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Vacuum", points_value=5)
        await store.async_complete_bonus_task(PERSON_ID, task_id)
        await store.async_complete_bonus_task(PERSON_ID, task_id)

        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["bonus_tasks"][0]["completions_this_week"] == 0

    async def test_rollover_clears_weekly_adjustments(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 3, "Reason A")
        await store.async_adjust_points(PERSON_ID, -1, "Reason B")
        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_adjustments"] == []

    async def test_rollover_returns_history_event(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_set_weekly_allotment(PERSON_ID, 10)
        await store.async_adjust_points(PERSON_ID, 7, "Earned")
        event = await store.async_apply_weekly_rollover(PERSON_ID)
        assert event["event_type"] == "weekly_rollover"
        assert event["person_id"] == PERSON_ID
        assert event["rolled_over_amount"] == 7
        assert event["new_allotment"] == 10

    async def test_rollover_with_zero_users_is_noop(self, fake_store):
        """Rolling over with no registered users should raise no errors."""
        store = await _loaded_store(fake_store)
        # No users — confirm get_all_person_ids is empty
        assert store.get_all_person_ids() == []

    async def test_rollover_accumulates_total_across_weeks(self, fake_store):
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 5, "Week 1")
        await store.async_apply_weekly_rollover(PERSON_ID)
        await store.async_adjust_points(PERSON_ID, 3, "Week 2")
        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        # total=5+3=8, weekly=0 (allotment=0)
        assert user["total_points"] == 8

    async def test_rollover_negative_weekly_points_handled(self, fake_store):
        """Weekly_points can be negative; total_points can go negative."""
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, -10, "Points debt")
        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["total_points"] == -10

    async def test_allotment_change_midweek_takes_effect_at_rollover(self, fake_store):
        """Changing allotment mid-week has no effect on current weekly_points."""
        store = await self._setup(fake_store)
        await store.async_adjust_points(PERSON_ID, 5, "Earned")
        # Change allotment mid-week — weekly_points must remain 5
        await store.async_set_weekly_allotment(PERSON_ID, 20)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == 5  # unchanged

        # After rollover, new allotment kicks in
        await store.async_apply_weekly_rollover(PERSON_ID)
        user = store.get_user_data(PERSON_ID)
        assert user["weekly_points"] == 20

    async def test_disable_then_reenable_bonus_task_preserves_count(self, fake_store):
        """Disabling then re-enabling a bonus task must not reset completions."""
        store = await self._setup(fake_store)
        task_id = await store.async_add_task(PERSON_ID, "bonus", "Chore", points_value=5)
        await store.async_complete_bonus_task(PERSON_ID, task_id)
        await store.async_update_task(PERSON_ID, "bonus", task_id, enabled=False)
        await store.async_update_task(PERSON_ID, "bonus", task_id, enabled=True)
        user = store.get_user_data(PERSON_ID)
        # Count must still be 1 — not reset by toggling enabled
        assert user["bonus_tasks"][0]["completions_this_week"] == 1


class TestRewards:
    async def _setup(self, fake_store):
        store = await _loaded_store(fake_store)
        await store.async_upsert_user_profile(PERSON_ID)
        await store.async_set_weekly_allotment(PERSON_ID, 7)
        store._data["users"][PERSON_ID]["total_points"] = 20
        store._data["users"][PERSON_ID]["weekly_points"] = 9
        return store

    async def test_profile_defaults_and_legacy_normalization(self, fake_store):
        fake_store.seed({"users": {PERSON_ID: {
            "weekly_allotment": 0, "total_points": 3, "weekly_points": 1,
            "base_tasks": [], "bonus_tasks": [], "weekly_adjustments": [],
        }}})
        store = await _loaded_store(fake_store)
        assert store.get_user_data(PERSON_ID)["rewards"] == []

    async def test_create_update_reassign_and_global_lookup(self, fake_store):
        store = await self._setup(fake_store)
        other = "person.kid_two"
        await store.async_upsert_user_profile(other)
        reward = await store.async_manage_reward(PERSON_ID, " Movie ", 10, "mdi:movie")
        assert reward["name"] == "Movie"
        updated = await store.async_manage_reward(other, "Game", 12, "mdi:gamepad",
                                                  description="Fun", reward_id=reward["id"])
        assert updated["person_id"] == other
        assert store.get_user_data(PERSON_ID)["rewards"] == []
        assert (await store.async_get_reward(reward["id"]))["description"] == "Fun"

    async def test_delete_unknown_reward_raises(self, fake_store):
        store = await self._setup(fake_store)
        with pytest.raises(KeyError, match="Unknown reward_id"):
            await store.async_delete_reward("missing")

    async def test_redeem_uses_banked_points_and_returns_immutable_snapshot(self, fake_store):
        store = await self._setup(fake_store)
        reward = await store.async_manage_reward(PERSON_ID, "Treat", 10, "mdi:gift")
        event = await store.async_redeem_reward(PERSON_ID, reward["id"])
        assert event | {} == event
        assert event["event_type"] == "reward_redemption"
        assert event["redemption_id"]
        assert event["person_id"] == PERSON_ID
        user = store.get_user_data(PERSON_ID)
        assert user["total_points"] == 10
        assert user["weekly_points"] == 9
        await store.async_manage_reward(PERSON_ID, "Renamed", 10, "mdi:gift",
                                         reward_id=reward["id"])
        assert event["reward_name"] == "Treat"

    async def test_redeem_rejects_disabled_wrong_owner_and_insufficient(self, fake_store):
        store = await self._setup(fake_store)
        other = "person.kid_two"
        await store.async_upsert_user_profile(other)
        reward = await store.async_manage_reward(PERSON_ID, "Treat", 10, "mdi:gift")
        await store.async_manage_reward(PERSON_ID, "Treat", 10, "mdi:gift",
                                        reward_id=reward["id"])
        store.get_user_data(PERSON_ID)["rewards"][0]["enabled"] = False
        with pytest.raises(ValueError, match="disabled"):
            await store.async_redeem_reward(PERSON_ID, reward["id"])
        store.get_user_data(PERSON_ID)["rewards"][0]["enabled"] = True
        with pytest.raises(ValueError, match="belong"):
            await store.async_redeem_reward(other, reward["id"])
        store._data["users"][PERSON_ID]["total_points"] = 5
        with pytest.raises(ValueError, match="Insufficient"):
            await store.async_redeem_reward(PERSON_ID, reward["id"])

    async def test_concurrent_redemption_has_exactly_one_success(self, fake_store):
        store = await self._setup(fake_store)
        reward = await store.async_manage_reward(PERSON_ID, "Treat", 15, "mdi:gift")
        results = await asyncio.gather(
            store.async_redeem_reward(PERSON_ID, reward["id"],),
            store.async_redeem_reward(PERSON_ID, reward["id"],),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert store.get_user_data(PERSON_ID)["total_points"] == 5
