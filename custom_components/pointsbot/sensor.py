"""Sensor platform for PointsBot — one entity per registered person."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_POINTSBOT_NEW_PERSON, SIGNAL_POINTSBOT_UPDATE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up PointsBot sensor entities from a config entry.

    Creates one :class:`PointsBotUserSensor` for each person currently in the
    store, then subscribes to :data:`SIGNAL_POINTSBOT_NEW_PERSON` so that
    additional entities can be added dynamically whenever a new person is
    discovered by a subsequent people-sync operation.
    """
    store = hass.data[DOMAIN][entry.entry_id]["store"]
    entry_id = entry.entry_id

    # Create initial entities for all persons already in the store.
    initial_entities = [
        PointsBotUserSensor(store, person_id, entry_id)
        for person_id in store.get_all_person_ids()
    ]
    async_add_entities(initial_entities)

    # Subscribe to dynamic person discovery.
    @callback
    def _async_new_person(person_id: str) -> None:
        _LOGGER.debug("Sensor platform: adding entity for new person %s", person_id)
        async_add_entities([PointsBotUserSensor(store, person_id, entry_id)])

    entry.async_on_unload(
        async_dispatcher_connect(
            hass,
            SIGNAL_POINTSBOT_NEW_PERSON.format(entry_id),
            _async_new_person,
        )
    )


class PointsBotUserSensor(SensorEntity):
    """Sensor entity representing a single PointsBot user's current point state.

    State (native_value) = lifetime total_points.
    Attributes expose the current week's data and task lists.
    Name and picture are resolved live from the person.* entity; they are
    never cached on this entity.
    """

    _attr_should_poll = False
    _attr_has_entity_name = False

    def __init__(
        self,
        store: Any,  # PointsBotStore — avoided circular import with Any
        person_id: str,
        entry_id: str,
    ) -> None:
        """Initialise the sensor."""
        self._store = store
        self._person_id = person_id
        self._entry_id = entry_id

        # Unique ID is stable across renames of the person entity.
        self._attr_unique_id = f"pointsbot_{person_id}"

        # Build a stable entity_id slug from the person domain suffix.
        # e.g. "person.kyle_smith" → "sensor.pointsbot_kyle_smith"
        slug = person_id.split(".", 1)[1] if "." in person_id else person_id
        self.entity_id = f"sensor.pointsbot_{slug}"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_added_to_hass(self) -> None:
        """Subscribe to the store-update dispatcher signal."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_POINTSBOT_UPDATE.format(self._entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        """Refresh HA state when a store mutation signals an update."""
        self.async_write_ha_state()

    # ------------------------------------------------------------------
    # SensorEntity properties
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        """Return a human-readable name, resolved live from the person entity."""
        state = self.hass.states.get(self._person_id)
        if state is not None:
            friendly = state.attributes.get("friendly_name")
            if friendly:
                return f"PointsBot {friendly}"
        return f"PointsBot {self._person_id}"

    @property
    def native_value(self) -> int:
        """Return the user's lifetime total points as the sensor state."""
        data = self._store.get_user_data(self._person_id)
        return data["total_points"] if data else 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return per-week data and live-resolved person metadata as attributes."""
        data = self._store.get_user_data(self._person_id)
        if data is None:
            return {"person_id": self._person_id}

        # Resolve name and picture live — never from a cache.
        state = self.hass.states.get(self._person_id)
        name: str | None = None
        picture: str | None = None
        if state is not None:
            name = state.attributes.get("friendly_name")
            picture = state.attributes.get("entity_picture")

        return {
            "weekly_points": data["weekly_points"],
            "weekly_allotment": data["weekly_allotment"],
            "base_tasks": data["base_tasks"],
            "bonus_tasks": data["bonus_tasks"],
            "weekly_adjustments": data["weekly_adjustments"],
            "person_id": self._person_id,
            "name": name,
            "picture": picture,
        }
