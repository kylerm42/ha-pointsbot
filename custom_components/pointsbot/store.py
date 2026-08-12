"""Persistent storage for PointsBot user profiles and task data."""

from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    EVENT_BONUS_COMPLETION,
    EVENT_BONUS_UNCOMPLETION,
    EVENT_MANUAL_ADJUSTMENT,
    EVENT_REWARD_REDEMPTION,
    EVENT_WEEKLY_ROLLOVER,
    STORAGE_KEY_DATA,
    STORAGE_VERSION,
    TASK_TYPE_BASE,
    TASK_TYPE_BONUS,
)

_LOGGER = logging.getLogger(__name__)

_EMPTY_DATA: dict[str, Any] = {"users": {}}


def _empty_user() -> dict[str, Any]:
    """Return a fresh user profile with all defaults."""
    return {
        "weekly_allotment": 0,
        "total_points": 0,
        "weekly_points": 0,
        "base_tasks": [],
        "bonus_tasks": [],
        "weekly_adjustments": [],
        "rewards": [],
        "pending_redemptions": [],
    }


class PointsBotStore:
    """Manages persistent storage for PointsBot user data.

    All mutation methods are serialised via an asyncio.Lock to prevent
    concurrent writes from corrupting in-memory state before it is flushed
    to disk.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the store (does not load data yet — call async_load)."""
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY_DATA)
        self._data: dict[str, Any] = {"users": {}}
        self._lock: asyncio.Lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load persisted data from disk into the in-memory cache."""
        stored = await self._store.async_load()
        if stored is None:
            self._data = {"users": {}}
        else:
            self._data = stored
        changed = False
        if not isinstance(self._data, dict):
            raise ValueError("PointsBot data must be an object")
        users = self._data.setdefault("users", {})
        if not isinstance(users, dict):
            raise ValueError("PointsBot users must be an object")
        for person_id, user in users.items():
            if not isinstance(user, dict):
                raise ValueError(f"Invalid profile for {person_id!r}")
            for key, value in _empty_user().items():
                if key not in user:
                    user[key] = copy.deepcopy(value)
                    changed = True
            if not isinstance(user["rewards"], list):
                raise ValueError(f"Invalid rewards catalog for {person_id!r}")
            if not isinstance(user["pending_redemptions"], list):
                raise ValueError(f"Invalid pending redemptions for {person_id!r}")
        if changed:
            await self.async_save()

    async def async_save(self) -> None:
        """Persist the in-memory cache to disk."""
        await self._store.async_save(self._data)

    # ------------------------------------------------------------------
    # User profile helpers
    # ------------------------------------------------------------------

    def _get_user(self, person_id: str) -> dict[str, Any] | None:
        """Return the user dict for *person_id*, or None if not found."""
        return self._data["users"].get(person_id)

    def _require_user(self, person_id: str) -> dict[str, Any]:
        """Return the user dict, raising KeyError if the user is unknown."""
        user = self._get_user(person_id)
        if user is None:
            raise KeyError(f"Unknown person_id: {person_id!r}")
        return user

    async def async_upsert_user_profile(self, person_id: str) -> None:
        """Create a user profile for *person_id* if one does not exist.

        If the profile already exists this is a no-op — no fields are
        overwritten.  Name and picture are intentionally not stored here;
        they are resolved live from the ``person.*`` entity on demand.
        """
        async with self._lock:
            if person_id not in self._data["users"]:
                self._data["users"][person_id] = _empty_user()
                await self.async_save()

    # ------------------------------------------------------------------
    # Task CRUD (unified, branches on task_type)
    # ------------------------------------------------------------------

    async def async_add_task(
        self,
        person_id: str,
        task_type: str,
        name: str,
        points_value: int | None = None,
    ) -> str:
        """Add a base or bonus task to a user's profile.

        Returns the newly assigned task UUID.

        For *task_type* ``"base"``:  creates ``{"id", "name", "done": False}``
        For *task_type* ``"bonus"``: creates
            ``{"id", "name", "points_value", "enabled": True,
               "completions_this_week": 0}``
        *points_value* is required when *task_type* is ``"bonus"``.
        """
        async with self._lock:
            user = self._require_user(person_id)
            task_id = str(uuid4())

            if task_type == TASK_TYPE_BASE:
                user["base_tasks"].append({"id": task_id, "name": name, "done": False})
            elif task_type == TASK_TYPE_BONUS:
                if points_value is None:
                    raise ValueError(
                        "points_value is required for bonus tasks"
                    )
                user["bonus_tasks"].append(
                    {
                        "id": task_id,
                        "name": name,
                        "points_value": points_value,
                        "enabled": True,
                        "completions_this_week": 0,
                    }
                )
            else:
                raise ValueError(f"Unknown task_type: {task_type!r}")

            await self.async_save()
            return task_id

    async def async_update_task(
        self,
        person_id: str,
        task_type: str,
        task_id: str,
        name: str | None = None,
        points_value: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Partially update a task.

        *name* applies to both task types.  *points_value* and *enabled*
        are silently ignored when *task_type* is ``"base"``.
        """
        async with self._lock:
            user = self._require_user(person_id)
            list_key = "base_tasks" if task_type == TASK_TYPE_BASE else "bonus_tasks"
            task = next((t for t in user[list_key] if t["id"] == task_id), None)
            if task is None:
                raise KeyError(
                    f"task_id {task_id!r} not found in {list_key} for {person_id!r}"
                )

            if name is not None:
                task["name"] = name

            if task_type == TASK_TYPE_BONUS:
                if points_value is not None:
                    task["points_value"] = points_value
                if enabled is not None:
                    task["enabled"] = enabled

            await self.async_save()

    async def async_delete_task(
        self, person_id: str, task_type: str, task_id: str
    ) -> None:
        """Hard-delete a task from the appropriate list."""
        async with self._lock:
            user = self._require_user(person_id)
            list_key = "base_tasks" if task_type == TASK_TYPE_BASE else "bonus_tasks"
            original_len = len(user[list_key])
            user[list_key] = [t for t in user[list_key] if t["id"] != task_id]
            if len(user[list_key]) == original_len:
                raise KeyError(
                    f"task_id {task_id!r} not found in {list_key} for {person_id!r}"
                )
            await self.async_save()

    # ------------------------------------------------------------------
    # Completion (type-specific — different side effects)
    # ------------------------------------------------------------------

    async def async_toggle_base_task(
        self, person_id: str, task_id: str
    ) -> bool:
        """Toggle the *done* flag on a base task.

        Returns the new value of *done*.  No points are affected.
        """
        async with self._lock:
            user = self._require_user(person_id)
            task = next(
                (t for t in user["base_tasks"] if t["id"] == task_id), None
            )
            if task is None:
                raise KeyError(
                    f"task_id {task_id!r} not found in base_tasks for {person_id!r}"
                )
            task["done"] = not task["done"]
            await self.async_save()
            return task["done"]

    async def async_complete_bonus_task(
        self, person_id: str, task_id: str
    ) -> dict[str, Any]:
        """Complete a bonus task.

        Increments ``completions_this_week`` and adds ``points_value`` to
        ``weekly_points``.  Raises ``ValueError`` if the task is disabled.

        Returns a dict suitable for appending to the history log::

            {
                "event_type": EVENT_BONUS_COMPLETION,
                "person_id": ...,
                "task_id": ...,
                "task_name": ...,
                "amount": <points_value>,
            }
        """
        async with self._lock:
            user = self._require_user(person_id)
            task = next(
                (t for t in user["bonus_tasks"] if t["id"] == task_id), None
            )
            if task is None:
                raise KeyError(
                    f"task_id {task_id!r} not found in bonus_tasks for {person_id!r}"
                )
            if not task.get("enabled", True):
                raise ValueError(
                    f"Bonus task {task_id!r} is disabled and cannot be completed"
                )

            task["completions_this_week"] += 1
            user["weekly_points"] += task["points_value"]
            await self.async_save()

            return {
                "event_type": EVENT_BONUS_COMPLETION,
                "person_id": person_id,
                "task_id": task_id,
                "task_name": task["name"],
                "amount": task["points_value"],
            }

    async def async_uncomplete_bonus_task(
        self, person_id: str, task_id: str
    ) -> dict[str, Any]:
        """Undo one completion of a bonus task.

        Decrements ``completions_this_week`` and subtracts ``points_value``
        from ``weekly_points``. Raises ``ValueError`` when there is no
        completion to undo.
        """
        async with self._lock:
            user = self._require_user(person_id)
            task = next(
                (t for t in user["bonus_tasks"] if t["id"] == task_id), None
            )
            if task is None:
                raise KeyError(
                    f"task_id {task_id!r} not found in bonus_tasks for {person_id!r}"
                )
            if task["completions_this_week"] <= 0:
                raise ValueError(
                    f"Bonus task {task_id!r} has no completion to undo"
                )

            task["completions_this_week"] -= 1
            user["weekly_points"] -= task["points_value"]
            await self.async_save()

            return {
                "event_type": EVENT_BONUS_UNCOMPLETION,
                "person_id": person_id,
                "task_id": task_id,
                "task_name": task["name"],
                "amount": -task["points_value"],
            }

    # ------------------------------------------------------------------
    # Points adjustment
    # ------------------------------------------------------------------

    async def async_adjust_points(
        self, person_id: str, amount: int, reason: str
    ) -> dict[str, Any]:
        """Apply a signed *amount* delta to a user's ``weekly_points``.

        *reason* must be a non-empty string.  Appends an entry to the
        user's ``weekly_adjustments`` list and returns a history-log-ready
        dict::

            {
                "event_type": EVENT_MANUAL_ADJUSTMENT,
                "person_id": ...,
                "amount": ...,
                "reason": ...,
            }
        """
        if not reason or not reason.strip():
            raise ValueError("reason must be a non-empty string")
        if amount == 0:
            raise ValueError("amount must be non-zero")

        async with self._lock:
            user = self._require_user(person_id)

            adjustment_id = str(uuid4())
            timestamp = dt_util.utcnow().isoformat()

            user["weekly_points"] += amount
            user["weekly_adjustments"].append(
                {
                    "id": adjustment_id,
                    "amount": amount,
                    "reason": reason,
                    "timestamp": timestamp,
                }
            )
            await self.async_save()

            return {
                "event_type": EVENT_MANUAL_ADJUSTMENT,
                "person_id": person_id,
                "amount": amount,
                "reason": reason,
            }

    # ------------------------------------------------------------------
    # Weekly allotment
    # ------------------------------------------------------------------

    async def async_set_weekly_allotment(
        self, person_id: str, amount: int
    ) -> None:
        """Set a user's weekly allotment.

        Takes effect at the next rollover, not retroactively.
        *amount* must be a non-negative integer.
        """
        if amount < 0:
            raise ValueError("weekly_allotment must be >= 0")
        async with self._lock:
            user = self._require_user(person_id)
            user["weekly_allotment"] = amount
            await self.async_save()

    # ------------------------------------------------------------------
    # Weekly rollover
    # ------------------------------------------------------------------

    async def async_apply_weekly_rollover(self, person_id: str) -> dict[str, Any]:
        """Apply the weekly rollover for a single user.

        Rollover logic (spec §2, §3):
        - ``total_points += weekly_points``
        - ``weekly_points`` resets to ``weekly_allotment``
        - All ``base_tasks[].done`` reset to ``False``
        - All ``bonus_tasks[].completions_this_week`` reset to ``0``
        - ``weekly_adjustments`` list cleared (permanent record lives in
          ``pointsbot_history``)

        Returns a history-log-ready dict with the rollover amounts.
        """
        async with self._lock:
            user = self._require_user(person_id)

            rolled_over_amount = user["weekly_points"]
            new_allotment = user["weekly_allotment"]

            user["total_points"] += rolled_over_amount
            user["weekly_points"] = new_allotment

            for task in user["base_tasks"]:
                task["done"] = False

            for task in user["bonus_tasks"]:
                task["completions_this_week"] = 0

            user["weekly_adjustments"] = []

            await self.async_save()

            return {
                "event_type": EVENT_WEEKLY_ROLLOVER,
                "person_id": person_id,
                "rolled_over_amount": rolled_over_amount,
                "new_allotment": new_allotment,
            }

    # ------------------------------------------------------------------
    # Reward catalog and redemption
    # ------------------------------------------------------------------

    def _find_reward(self, reward_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Find a reward globally, returning its owner profile and record."""
        for user in self._data["users"].values():
            for reward in user.get("rewards", []):
                if reward.get("id") == reward_id:
                    return user, reward
        raise KeyError(f"Unknown reward_id: {reward_id!r}")

    async def async_manage_reward(
        self,
        person_id: str,
        name: str,
        cost: int,
        icon: str,
        description: str = "",
        reward_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or fully update a globally unique person-owned reward."""
        if not isinstance(cost, int) or isinstance(cost, bool) or cost <= 0:
            raise ValueError("reward cost must be a positive integer")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("reward name must be non-empty")
        if not isinstance(icon, str) or not icon.strip():
            raise ValueError("reward icon must be non-empty")
        import re
        if not re.fullmatch(r"mdi:[a-z0-9][a-z0-9-]*", icon.strip()):
            raise ValueError("reward icon must be a valid MDI icon (mdi:name)")
        if not isinstance(description, str):
            raise ValueError("reward description must be a string")
        async with self._lock:
            target_user = self._require_user(person_id)
            now = dt_util.utcnow().isoformat()
            if reward_id is None:
                reward = {
                    "id": str(uuid4()), "name": name.strip(), "cost": cost,
                    "icon": icon.strip(), "enabled": True,
                    "description": description, "created": now, "modified": now,
                    "person_id": person_id,
                }
                target_user["rewards"].append(reward)
            else:
                old_user, reward = self._find_reward(reward_id)
                if old_user is not target_user:
                    old_user["rewards"].remove(reward)
                    target_user["rewards"].append(reward)
                reward.update({"name": name.strip(), "cost": cost, "icon": icon.strip(),
                               "description": description, "modified": now,
                               "person_id": person_id})
            await self.async_save()
            return copy.deepcopy(reward)

    async def async_get_reward(self, reward_id: str) -> dict[str, Any]:
        async with self._lock:
            _, reward = self._find_reward(reward_id)
            return copy.deepcopy(reward)

    async def async_delete_reward(self, reward_id: str) -> None:
        async with self._lock:
            user, reward = self._find_reward(reward_id)
            user["rewards"].remove(reward)
            await self.async_save()

    async def async_redeem_reward(self, person_id: str, reward_id: str) -> dict[str, Any]:
        """Redeem an enabled reward against banked points only."""
        async with self._lock:
            user = self._require_user(person_id)
            owner, reward = self._find_reward(reward_id)
            if owner is not user or reward.get("person_id") != person_id:
                raise ValueError("Reward does not belong to this person")
            if not reward.get("enabled", True):
                raise ValueError("Reward is disabled")
            if user["total_points"] < reward["cost"]:
                raise ValueError("Insufficient banked points")
            redemption_id = str(uuid4())
            event = {
                "event_type": EVENT_REWARD_REDEMPTION,
                "redemption_id": redemption_id,
                "person_id": person_id,
                "reward_id": reward["id"],
                "reward_name": reward["name"],
                "cost": reward["cost"],
                "amount": -reward["cost"],
            }
            user["pending_redemptions"].append(copy.deepcopy(event))
            await self.async_save()
            user["total_points"] -= reward["cost"]
            await self.async_save()
            return event

    async def async_commit_redemption(self, redemption_id: str) -> None:
        """Remove a redemption marker after its audit event is durable."""
        async with self._lock:
            for user in self._data["users"].values():
                before = len(user["pending_redemptions"])
                user["pending_redemptions"] = [
                    item for item in user["pending_redemptions"]
                    if item.get("redemption_id") != redemption_id
                ]
                if len(user["pending_redemptions"]) != before:
                    await self.async_save()
                    return
            raise KeyError(f"Unknown redemption_id: {redemption_id!r}")

    async def async_reconcile_redemptions(self, history_events: list[dict[str, Any]]) -> None:
        """Undo deductions whose audit event was not durably written."""
        audited = {event.get("redemption_id") for event in history_events}
        async with self._lock:
            changed = False
            for user in self._data["users"].values():
                pending = user["pending_redemptions"]
                for event in pending:
                    if event.get("redemption_id") not in audited:
                        user["total_points"] += event["cost"]
                    changed = True
                user["pending_redemptions"] = []
            if changed:
                await self.async_save()

    async def async_restore_redemption(self, person_id: str, amount: int) -> None:
        """Restore a balance when the companion history write fails."""
        async with self._lock:
            user = self._require_user(person_id)
            user["total_points"] += amount
            await self.async_save()

    # ------------------------------------------------------------------
    # Read helpers (no lock needed — reads are non-mutating)
    # ------------------------------------------------------------------

    def get_all_person_ids(self) -> list[str]:
        """Return all registered person IDs."""
        return list(self._data["users"].keys())

    def get_user_data(self, person_id: str) -> dict[str, Any] | None:
        """Return a shallow copy of the user dict, or None if not found."""
        user = self._data["users"].get(person_id)
        if user is None:
            return None
        return dict(user)
