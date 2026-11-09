"""Wachturm setup and configuration options."""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .configuration import default_config_json, parse_configuration_json
from .const import DOMAIN, NAME


class AlarmzentraleConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one Wachturm instance."""

    VERSION = 1

    async def async_step_user(self, user_input: dict | None = None) -> FlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        if user_input is not None:
            return self.async_create_entry(title=user_input["name"], data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required("name", default=NAME): str}),
        )

    @staticmethod
    def async_get_options_flow(config_entry):
        return AlarmzentraleOptionsFlow()


class AlarmzentraleOptionsFlow(config_entries.OptionsFlowWithReload):
    """Interim JSON area/zone configuration. Refuses malformed config."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                parse_configuration_json(user_input["configuration_json"])
            except ValueError:
                errors["base"] = "invalid_configuration"
            else:
                return self.async_create_entry(title="", data=user_input)
        current = (
            user_input["configuration_json"] if user_input is not None
            else self.config_entry.options.get("configuration_json", default_config_json())
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Required("configuration_json", default=current): str}),
            errors=errors,
        )
