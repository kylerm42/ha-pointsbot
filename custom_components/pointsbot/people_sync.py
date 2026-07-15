"""Synchronise Home Assistant person.* entities into PointsBot user profiles."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_POINTSBOT_NEW_PERSON

if TYPE_CHECKING:
    from .store import PointsBotStore

_LOGGER = logging.getLogger(__name__)


async def async_sync_people(
    hass: HomeAssistant,
    store: PointsBotStore,
    entry_id: str | None = None,
) -> list[str]:
    """Enumerate person.* entities and upsert a PointsBotUser for each.

    Creates a user profile (weekly_allotment=0) for any person not yet known
    to the store.  Profiles for existing persons are left unchanged (no fields
    are overwritten).  Persons whose person.* entity has disappeared are never
    removed from the store.

    If *entry_id* is provided, a :data:`SIGNAL_POINTSBOT_NEW_PERSON` dispatcher
    signal is sent for each genuinely new profile so that the sensor platform
    can add a corresponding entity dynamically.

    Returns the list of person_ids that were touched (i.e., all currently
    visible persons, regardless of whether they were already known).
    """
    existing_ids: set[str] = set(store.get_all_person_ids())
    states = hass.states.async_all("person")
    touched: list[str] = []

    for state in states:
        person_id = state.entity_id
        is_new = person_id not in existing_ids
        await store.async_upsert_user_profile(person_id)
        touched.append(person_id)

        if is_new and entry_id is not None:
            _LOGGER.debug("New person discovered, dispatching: %s", person_id)
            async_dispatcher_send(
                hass,
                SIGNAL_POINTSBOT_NEW_PERSON.format(entry_id),
                person_id,
            )

    _LOGGER.debug("People sync complete: %d person(s) touched", len(touched))
    return touched
