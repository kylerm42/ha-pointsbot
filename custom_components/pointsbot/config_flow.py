"""Config flow for the PointsBot integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class PointsBotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the PointsBot config flow.

    Single-instance only; no user input beyond confirmation is required.
    No options flow exists — all configuration is per-user via service calls.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(title="PointsBot", data={})

        return self.async_show_form(step_id="user")
