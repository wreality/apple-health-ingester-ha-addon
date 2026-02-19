"""Config flow for Health Data Ingester."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow

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
