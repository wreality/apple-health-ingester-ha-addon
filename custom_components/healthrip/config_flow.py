"""Config flow for Health Data Ingester."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_INFLUXDB_BUCKET,
    CONF_INFLUXDB_ORG,
    CONF_INFLUXDB_TOKEN,
    CONF_INFLUXDB_URL,
    DOMAIN,
)


class HealthIngesterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Health Data Ingester."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow handler."""
        return HealthIngesterOptionsFlow(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is not None:
            return self.async_create_entry(
                title="Health Data Ingester",
                data=user_input,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INFLUXDB_URL, default="http://a0d7b954-influxdb:8086"): str,
                    vol.Required(CONF_INFLUXDB_TOKEN): str,
                    vol.Required(CONF_INFLUXDB_ORG, default="homeassistant"): str,
                    vol.Required(CONF_INFLUXDB_BUCKET, default="health"): str,
                }
            ),
        )


class HealthIngesterOptionsFlow(OptionsFlow):
    """Handle options for Health Data Ingester."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            self.hass.config_entries.async_update_entry(
                self._config_entry,
                data={**self._config_entry.data, **user_input},
            )
            return self.async_create_entry(title="", data={})

        current = self._config_entry.data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_INFLUXDB_URL, default=current.get(CONF_INFLUXDB_URL, "")): str,
                    vol.Required(CONF_INFLUXDB_TOKEN, default=current.get(CONF_INFLUXDB_TOKEN, "")): str,
                    vol.Required(CONF_INFLUXDB_ORG, default=current.get(CONF_INFLUXDB_ORG, "")): str,
                    vol.Required(CONF_INFLUXDB_BUCKET, default=current.get(CONF_INFLUXDB_BUCKET, "")): str,
                }
            ),
        )
