from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_ENABLE_CALENDAR,
    CONF_REQUESTED_BY,
    CONF_TOURS,
    DEFAULT_ENABLE_CALENDAR,
    DOMAIN,
    TOUR_CHOICES,
)


def _tour_selector() -> SelectSelector:
    # TOUR_CHOICES is {"Main tour": "main", ...}
    # We want the UI to show labels but store the API codes.
    options = [{"label": label, "value": code} for label, code in TOUR_CHOICES.items()]
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            multiple=True,
            mode=SelectSelectorMode.DROPDOWN,
        )
    )


class SnookerOrgConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}

        if user_input is not None:
            if not user_input.get(CONF_TOURS):
                errors[CONF_TOURS] = "select_at_least_one_tour"
            else:
                return self.async_create_entry(title="Snooker.org", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_REQUESTED_BY): str,
                vol.Required(CONF_TOURS): _tour_selector(),
                vol.Optional(CONF_ENABLE_CALENDAR, default=DEFAULT_ENABLE_CALENDAR): bool,
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SnookerOrgOptionsFlow()


class SnookerOrgOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}

        if user_input is not None:
            if not user_input.get(CONF_TOURS):
                errors[CONF_TOURS] = "select_at_least_one_tour"
            else:
                return self.async_create_entry(title="", data=user_input)

        current_tours = self.config_entry.options.get(
            CONF_TOURS, self.config_entry.data.get(CONF_TOURS, [])
        )
        current_enable_cal = self.config_entry.options.get(
            CONF_ENABLE_CALENDAR,
            self.config_entry.data.get(CONF_ENABLE_CALENDAR, DEFAULT_ENABLE_CALENDAR),
        )

        schema = vol.Schema(
            {
                vol.Required(CONF_TOURS, default=current_tours): _tour_selector(),
                vol.Optional(CONF_ENABLE_CALENDAR, default=current_enable_cal): bool,
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)