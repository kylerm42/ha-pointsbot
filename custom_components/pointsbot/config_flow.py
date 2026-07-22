"""Config flow for the PointsBot integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.selector import IconSelector, TextSelector

from .const import DEFAULT_CONFIG_ICON, DEFAULT_CONFIG_TITLE, DOMAIN


class PointsBotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the PointsBot config flow.

    Single-instance only. Points data is configured per-user via service calls;
    the config flow controls the integration's presentation metadata.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial setup step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            title = str(user_input["title"]).strip()
            icon = str(user_input["icon"]).strip()
            if not title:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._schema(user_input),
                    errors={"title": "title_required"},
                )
            if not icon:
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._schema(user_input),
                    errors={"icon": "icon_required"},
                )
            return self.async_create_entry(
                title=title, data={"title": title, "icon": icon}
            )

        return self.async_show_form(step_id="user", data_schema=self._schema())

    @staticmethod
    def _schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
        """Return the initial configuration schema."""
        values = user_input or {}
        return vol.Schema(
            {
                vol.Required("title", default=values.get("title", DEFAULT_CONFIG_TITLE)):
                    TextSelector(),
                vol.Required("icon", default=values.get("icon", DEFAULT_CONFIG_ICON)):
                    IconSelector(),
            }
        )
