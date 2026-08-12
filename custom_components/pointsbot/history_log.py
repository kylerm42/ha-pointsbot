"""Append-only audit-log storage for PointsBot point-affecting events."""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import STORAGE_KEY_HISTORY, STORAGE_VERSION

_LOGGER = logging.getLogger(__name__)


class PointsBotHistoryLog:
    """Manages the uncapped, append-only ``pointsbot_history`` Store.

    This class is intentionally minimal: it writes events and reads them
    back.  There is no trimming, pagination, or indexing — at family scale
    this file remains negligible in size indefinitely.

    Every event written via :meth:`async_append` receives an auto-assigned
    ``id`` (UUID4) and ``timestamp`` (UTC ISO-8601) so callers need not
    supply these.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialise the history log (does not load data — call async_load)."""
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY_HISTORY)
        self._data: dict[str, Any] = {"events": []}

    # ------------------------------------------------------------------
    # Load
    # ------------------------------------------------------------------

    async def async_load(self) -> None:
        """Load persisted event history from disk into the in-memory cache."""
        stored = await self._store.async_load()
        if stored is None:
            self._data = {"events": []}
        else:
            self._data = stored
        # Preserve all existing event dictionaries while tolerating legacy or
        # partially initialized history payloads.
        if not isinstance(self._data.get("events"), list):
            self._data["events"] = []

    # ------------------------------------------------------------------
    # Append
    # ------------------------------------------------------------------

    async def async_append(self, event: dict[str, Any]) -> str:
        """Append *event* to the history log.

        Auto-assigns ``id`` (UUID4) and ``timestamp`` (UTC ISO-8601) on the
        event dict before storing.  The enriched event is written immediately
        to the backing Store.

        Returns the assigned event ``id``.
        """
        event_id = str(uuid4())
        timestamp = dt_util.utcnow().isoformat()

        enriched = {
            "id": event_id,
            "timestamp": timestamp,
            **event,
        }
        self._data["events"].append(enriched)
        await self._store.async_save(self._data)
        return event_id

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_all_events(self) -> list[dict[str, Any]]:
        """Return a copy of all events (oldest first)."""
        return list(self._data["events"])
