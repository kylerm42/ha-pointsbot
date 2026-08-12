"""The PointsBot integration."""

from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import DOMAIN  # noqa: F401
from .history_log import PointsBotHistoryLog
from .people_sync import async_sync_people
from .services import async_register_services
from .store import PointsBotStore
from .weekly_reset import async_perform_weekly_reset

_LOGGER = logging.getLogger(__name__)

_PLATFORMS = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up PointsBot from a config entry.

    Orchestration order:
    1. Instantiate and load PointsBotStore and PointsBotHistoryLog.
    2. Sync person.* entities into user profiles.
    3. Forward setup to the SENSOR platform.
    4. Register the Monday-midnight weekly-reset time-change listener.
    """
    store = PointsBotStore(hass)
    await store.async_load()

    history_log = PointsBotHistoryLog(hass)
    await history_log.async_load()
    await store.async_reconcile_redemptions(history_log.get_all_events())

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "store": store,
        "history_log": history_log,
    }

    # Sync person entities — this may dispatch SIGNAL_POINTSBOT_NEW_PERSON, but
    # the sensor platform is not yet listening (it forwards below).  Initial
    # entities are created from store contents in sensor.async_setup_entry.
    await async_sync_people(hass, store)

    await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)

    # Register services — guarded internally against duplicate registration.
    async_register_services(hass)

    # Register weekly reset: fires every day at 00:00:00, Monday guard inside.
    async def _weekly_reset_callback(now: datetime) -> None:
        if now.weekday() != 0:  # 0 = Monday
            return
        _LOGGER.info("Weekly reset triggered (Monday midnight)")
        await async_perform_weekly_reset(hass, store, history_log, entry.entry_id)

    unsub = async_track_time_change(
        hass, _weekly_reset_callback, hour=0, minute=0, second=0
    )
    hass.data[DOMAIN][entry.entry_id]["unsub_weekly_reset"] = unsub

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a PointsBot config entry.

    Cancels the weekly-reset time-change listener and unloads the SENSOR
    platform before removing entry data from hass.data.
    """
    data = hass.data[DOMAIN].get(entry.entry_id, {})

    unsub = data.get("unsub_weekly_reset")
    if unsub is not None:
        unsub()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, _PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    return unload_ok
