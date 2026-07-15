"""Service handlers for the PointsBot integration.

All nine services delegate to PointsBotStore / PointsBotHistoryLog / helper
modules established in Phases 1a and 1b, then dispatch SIGNAL_POINTSBOT_UPDATE
so that affected sensor entities refresh their HA state immediately.

Validation errors are raised as homeassistant.exceptions.ServiceValidationError
so that HA surfaces them as user-readable messages in the UI and logs.
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    DOMAIN,
    SERVICE_ADJUST_POINTS,
    SERVICE_ADD_TASK,
    SERVICE_COMPLETE_BONUS_TASK,
    SERVICE_DELETE_TASK,
    SERVICE_RUN_WEEKLY_RESET,
    SERVICE_SET_WEEKLY_ALLOTMENT,
    SERVICE_SYNC_PEOPLE,
    SERVICE_TOGGLE_BASE_TASK,
    SERVICE_UPDATE_TASK,
    SIGNAL_POINTSBOT_UPDATE,
    TASK_TYPE_BASE,
    TASK_TYPE_BONUS,
)
from .people_sync import async_sync_people
from .weekly_reset import async_perform_weekly_reset

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_components(hass: HomeAssistant) -> tuple:
    """Return (store, history_log, entry_id) from hass.data.

    Raises ServiceValidationError if the integration is not set up.
    """
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data:
        raise ServiceValidationError(
            "PointsBot integration is not set up. Install it via Settings → Integrations."
        )
    entry_id = next(iter(domain_data))
    entry_data = domain_data[entry_id]
    return entry_data["store"], entry_data["history_log"], entry_id


def _dispatch_update(hass: HomeAssistant, entry_id: str) -> None:
    """Send the store-update dispatcher signal to refresh all sensor entities."""
    async_dispatcher_send(hass, SIGNAL_POINTSBOT_UPDATE.format(entry_id))


def _require_person(store, person_id: str) -> None:
    """Raise ServiceValidationError if person_id is unknown to the store."""
    if store.get_user_data(person_id) is None:
        raise ServiceValidationError(
            f"Unknown person_id: '{person_id}'. "
            "Run the pointsbot.sync_people service to import current HA persons, "
            "or verify the person_id matches a person.* entity."
        )


def _require_field(call: ServiceCall, field: str) -> object:
    """Return a required field value, raising ServiceValidationError if absent."""
    value = call.data.get(field)
    if value is None:
        raise ServiceValidationError(
            f"Required field '{field}' is missing from the service call."
        )
    return value


# ---------------------------------------------------------------------------
# Service handlers
# ---------------------------------------------------------------------------


async def handle_sync_people(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.sync_people — re-sync all HA person.* entities."""
    store, _history_log, entry_id = _get_components(hass)
    touched = await async_sync_people(hass, store, entry_id)
    _LOGGER.info("sync_people: synced %d person(s): %s", len(touched), touched)
    _dispatch_update(hass, entry_id)


async def handle_adjust_points(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.adjust_points — apply a signed point delta to a user."""
    store, history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    amount = call.data.get("amount")
    reason = call.data.get("reason", "")

    if amount is None:
        raise ServiceValidationError(
            "Required field 'amount' is missing from the service call."
        )

    # Coerce to int — HA selectors may deliver floats for number fields.
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ServiceValidationError(
            f"'amount' must be an integer, got {amount!r}."
        )

    if amount == 0:
        raise ServiceValidationError(
            "'amount' must be non-zero. Use a positive value to award points or a "
            "negative value to deduct them."
        )

    if not isinstance(reason, str) or not reason.strip():
        raise ServiceValidationError(
            "'reason' must be a non-empty string describing why the adjustment was made "
            "(e.g. \"Left dirty dishes out\")."
        )

    _require_person(store, person_id)

    try:
        event = await store.async_adjust_points(person_id, amount, reason)
    except (KeyError, ValueError) as exc:
        raise ServiceValidationError(str(exc)) from exc

    await history_log.async_append(event)
    _dispatch_update(hass, entry_id)
    _LOGGER.debug("adjust_points: %s %+d (%s)", person_id, amount, reason)


async def handle_set_weekly_allotment(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.set_weekly_allotment — set a user's weekly point grant."""
    store, _history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    amount = call.data.get("amount")

    if amount is None:
        raise ServiceValidationError(
            "Required field 'amount' is missing from the service call."
        )

    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise ServiceValidationError(
            f"'amount' must be a non-negative integer, got {amount!r}."
        )

    if amount < 0:
        raise ServiceValidationError(
            f"'amount' must be >= 0 (got {amount}). "
            "Use 0 to opt this person out of the weekly allotment."
        )

    _require_person(store, person_id)

    try:
        await store.async_set_weekly_allotment(person_id, amount)
    except (KeyError, ValueError) as exc:
        raise ServiceValidationError(str(exc)) from exc

    _dispatch_update(hass, entry_id)
    _LOGGER.debug("set_weekly_allotment: %s → %d", person_id, amount)


async def handle_add_task(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.add_task — create a base or bonus task for a user."""
    store, _history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    task_type = str(_require_field(call, "task_type"))
    name = str(_require_field(call, "name")).strip()
    points_value = call.data.get("points_value")

    if task_type not in (TASK_TYPE_BASE, TASK_TYPE_BONUS):
        raise ServiceValidationError(
            f"'task_type' must be 'base' or 'bonus', got '{task_type}'."
        )

    if not name:
        raise ServiceValidationError(
            "'name' must be a non-empty string."
        )

    if task_type == TASK_TYPE_BONUS:
        if points_value is None:
            raise ServiceValidationError(
                "'points_value' is required when 'task_type' is 'bonus'. "
                "Specify how many points completing this task awards."
            )
        try:
            points_value = int(points_value)
        except (TypeError, ValueError):
            raise ServiceValidationError(
                f"'points_value' must be an integer, got {points_value!r}."
            )
        if points_value <= 0:
            raise ServiceValidationError(
                f"'points_value' must be a positive integer (got {points_value}). "
                "Bonus tasks must be worth at least 1 point."
            )

    if task_type == TASK_TYPE_BASE and points_value is not None:
        raise ServiceValidationError(
            "'points_value' is not applicable to base tasks (task_type='base'). "
            "Base tasks are informational checkmarks with no point value. "
            "Use task_type='bonus' to create a points-granting task."
        )

    _require_person(store, person_id)

    try:
        task_id = await store.async_add_task(
            person_id, task_type, name, points_value
        )
    except (KeyError, ValueError) as exc:
        raise ServiceValidationError(str(exc)) from exc

    _dispatch_update(hass, entry_id)
    _LOGGER.debug("add_task: %s %s '%s' → %s", person_id, task_type, name, task_id)


async def handle_update_task(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.update_task — partially update an existing task."""
    store, _history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    task_type = str(_require_field(call, "task_type"))
    task_id = str(_require_field(call, "task_id"))
    name = call.data.get("name")
    points_value = call.data.get("points_value")
    enabled = call.data.get("enabled")

    if task_type not in (TASK_TYPE_BASE, TASK_TYPE_BONUS):
        raise ServiceValidationError(
            f"'task_type' must be 'base' or 'bonus', got '{task_type}'."
        )

    # Detect mismatched bonus-only fields on a base task.
    if task_type == TASK_TYPE_BASE:
        if points_value is not None:
            raise ServiceValidationError(
                "'points_value' is not applicable to base tasks (task_type='base'). "
                "Base tasks have no point value. "
                "Use task_type='bonus' if you meant to update a bonus task."
            )
        if enabled is not None:
            raise ServiceValidationError(
                "'enabled' is not applicable to base tasks (task_type='base'). "
                "Use task_type='bonus' if you meant to update a bonus task."
            )

    if name is not None:
        name = str(name).strip()
        if not name:
            raise ServiceValidationError("'name' must be a non-empty string.")

    if points_value is not None:
        try:
            points_value = int(points_value)
        except (TypeError, ValueError):
            raise ServiceValidationError(
                f"'points_value' must be an integer, got {points_value!r}."
            )
        if points_value <= 0:
            raise ServiceValidationError(
                f"'points_value' must be a positive integer (got {points_value})."
            )

    if name is None and points_value is None and enabled is None:
        raise ServiceValidationError(
            "At least one of 'name', 'points_value', or 'enabled' must be provided "
            "to update a task."
        )

    _require_person(store, person_id)

    try:
        await store.async_update_task(
            person_id, task_type, task_id, name=name,
            points_value=points_value, enabled=enabled,
        )
    except KeyError as exc:
        raise ServiceValidationError(
            f"Task not found: task_id '{task_id}' does not exist in "
            f"{task_type}_tasks for person '{person_id}'. "
            "Verify the task_id and task_type are correct."
        ) from exc
    except ValueError as exc:
        raise ServiceValidationError(str(exc)) from exc

    _dispatch_update(hass, entry_id)
    _LOGGER.debug("update_task: %s %s %s", person_id, task_type, task_id)


async def handle_delete_task(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.delete_task — permanently remove a task."""
    store, _history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    task_type = str(_require_field(call, "task_type"))
    task_id = str(_require_field(call, "task_id"))

    if task_type not in (TASK_TYPE_BASE, TASK_TYPE_BONUS):
        raise ServiceValidationError(
            f"'task_type' must be 'base' or 'bonus', got '{task_type}'."
        )

    _require_person(store, person_id)

    try:
        await store.async_delete_task(person_id, task_type, task_id)
    except KeyError as exc:
        raise ServiceValidationError(
            f"Task not found: task_id '{task_id}' does not exist in "
            f"{task_type}_tasks for person '{person_id}'. "
            "Verify the task_id and task_type are correct."
        ) from exc

    _dispatch_update(hass, entry_id)
    _LOGGER.debug("delete_task: %s %s %s", person_id, task_type, task_id)


async def handle_toggle_base_task(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.toggle_base_task — flip the done flag on a base task."""
    store, _history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    task_id = str(_require_field(call, "task_id"))

    _require_person(store, person_id)

    try:
        new_done = await store.async_toggle_base_task(person_id, task_id)
    except KeyError as exc:
        raise ServiceValidationError(
            f"Task not found: task_id '{task_id}' does not exist in "
            f"base_tasks for person '{person_id}'. "
            "Verify the task_id is correct and the task is a base task, not a bonus task."
        ) from exc

    _dispatch_update(hass, entry_id)
    _LOGGER.debug(
        "toggle_base_task: %s %s → done=%s", person_id, task_id, new_done
    )


async def handle_complete_bonus_task(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.complete_bonus_task — record a bonus task completion."""
    store, history_log, entry_id = _get_components(hass)

    person_id = str(_require_field(call, "person_id"))
    task_id = str(_require_field(call, "task_id"))

    _require_person(store, person_id)

    try:
        event = await store.async_complete_bonus_task(person_id, task_id)
    except KeyError as exc:
        raise ServiceValidationError(
            f"Task not found: task_id '{task_id}' does not exist in "
            f"bonus_tasks for person '{person_id}'. "
            "Verify the task_id is correct and the task is a bonus task, not a base task."
        ) from exc
    except ValueError as exc:
        raise ServiceValidationError(
            f"Cannot complete task '{task_id}': it is currently disabled. "
            "Re-enable the task via pointsbot.update_task (enabled: true) before completing it."
        ) from exc

    await history_log.async_append(event)
    _dispatch_update(hass, entry_id)
    _LOGGER.debug(
        "complete_bonus_task: %s %s +%d pts",
        person_id, task_id, event["amount"]
    )


async def handle_run_weekly_reset(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle pointsbot.run_weekly_reset — trigger the full weekly rollover now."""
    store, history_log, entry_id = _get_components(hass)
    await async_perform_weekly_reset(hass, store, history_log, entry_id)
    _LOGGER.info("run_weekly_reset: manual rollover complete")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_HANDLERS = {
    SERVICE_SYNC_PEOPLE: handle_sync_people,
    SERVICE_ADJUST_POINTS: handle_adjust_points,
    SERVICE_SET_WEEKLY_ALLOTMENT: handle_set_weekly_allotment,
    SERVICE_ADD_TASK: handle_add_task,
    SERVICE_UPDATE_TASK: handle_update_task,
    SERVICE_DELETE_TASK: handle_delete_task,
    SERVICE_TOGGLE_BASE_TASK: handle_toggle_base_task,
    SERVICE_COMPLETE_BONUS_TASK: handle_complete_bonus_task,
    SERVICE_RUN_WEEKLY_RESET: handle_run_weekly_reset,
}


def async_register_services(hass: HomeAssistant) -> None:
    """Register all PointsBot services with Home Assistant.

    Guards against duplicate registration — safe to call from async_setup_entry
    even though only one config entry ever exists.
    """
    for service_name, handler in _HANDLERS.items():
        if not hass.services.has_service(DOMAIN, service_name):
            hass.services.async_register(
                DOMAIN,
                service_name,
                lambda call, h=handler: h(hass, call),
            )
            _LOGGER.debug("Registered service: %s.%s", DOMAIN, service_name)
