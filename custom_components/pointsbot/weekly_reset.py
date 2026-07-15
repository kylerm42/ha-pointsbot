"""Weekly rollover logic for PointsBot."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import SIGNAL_POINTSBOT_UPDATE

if TYPE_CHECKING:
    from .history_log import PointsBotHistoryLog
    from .store import PointsBotStore

_LOGGER = logging.getLogger(__name__)


async def async_perform_weekly_reset(
    hass: HomeAssistant,
    store: PointsBotStore,
    history_log: PointsBotHistoryLog,
    entry_id: str | None = None,
) -> None:
    """Execute the weekly rollover for every registered user.

    For each user in the store:

    1. Applies the rollover via :meth:`PointsBotStore.async_apply_weekly_rollover`
       (which: accumulates weekly_points into total_points, resets weekly_points
       to weekly_allotment, resets base-task done flags, resets bonus-task
       completion counts, and clears the weekly_adjustments list).
    2. Appends a ``weekly_rollover`` event to the history log.

    After all users have been processed, dispatches
    :data:`SIGNAL_POINTSBOT_UPDATE` (formatted with *entry_id*) so that all
    sensor entities refresh their state.  If *entry_id* is None no signal is
    sent (useful for isolated unit testing).

    This function is idempotent with respect to code path — it is the same
    function called by both the scheduled Monday-midnight callback and the
    ``pointsbot.run_weekly_reset`` service.
    """
    person_ids = store.get_all_person_ids()
    _LOGGER.debug("Weekly reset: processing %d user(s)", len(person_ids))

    for person_id in person_ids:
        event = await store.async_apply_weekly_rollover(person_id)
        await history_log.async_append(event)
        _LOGGER.debug(
            "Rolled over %s: +%d points, new allotment %d",
            person_id,
            event["rolled_over_amount"],
            event["new_allotment"],
        )

    if entry_id is not None:
        async_dispatcher_send(hass, SIGNAL_POINTSBOT_UPDATE.format(entry_id))
