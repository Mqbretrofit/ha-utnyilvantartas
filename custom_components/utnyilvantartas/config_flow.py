from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .api import AlapnyomkovetesAuthError, AlapnyomkovetesClient, AlapnyomkovetesError
from .const import (
    CONF_DEVICE_ID,
    CONF_HOME_ZONE,
    CONF_HOME_GPS_RADIUS,
    CONF_KELIO_ENTITY,
    CONF_KELIO_MONTH_ENTITY,
    CONF_ENDPOINT_WINDOW_KM,
    CONF_SCAN_INTERVAL,
    CONF_VEHICLE_NAME,
    CONF_WORK_ZONE,
    CONF_WORK_GPS_RADIUS,
    DEFAULT_HOME_ZONE,
    DEFAULT_HOME_GPS_RADIUS,
    DEFAULT_KELIO_ENTITY,
    DEFAULT_KELIO_MONTH_ENTITY,
    DEFAULT_ENDPOINT_WINDOW_KM,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_WORK_ZONE,
    DEFAULT_WORK_GPS_RADIUS,
    CONF_REPORT_COMPANY_NAME, CONF_REPORT_COMPANY_ADDRESS, CONF_REPORT_TAX_NUMBER,
    CONF_REPORT_EMPLOYEE_NAME, CONF_REPORT_PRIVATE_VEHICLE, CONF_REPORT_VEHICLE_TYPE,
    CONF_REPORT_FUEL_TYPE, CONF_REPORT_FUEL_CONSUMPTION, CONF_REPORT_ENGINE_CC, CONF_REPORT_START_ODOMETER,
    CONF_REPORT_HOME_LABEL, CONF_REPORT_HOME_ADDRESS, CONF_REPORT_FUEL_PRICE, CONF_REPORT_TRIP_NATURE,
    CONF_COMMUTE_ONE_WAY_KM, CONF_REIMBURSEMENT_HUF_PER_KM,
    CONF_EMAIL_RECIPIENT, CONF_EMAIL_SUBJECT, CONF_EMAIL_BODY,
    DEFAULT_REPORT_COMPANY_NAME, DEFAULT_REPORT_COMPANY_ADDRESS, DEFAULT_REPORT_TAX_NUMBER,
    DEFAULT_REPORT_EMPLOYEE_NAME, DEFAULT_REPORT_PRIVATE_VEHICLE, DEFAULT_REPORT_VEHICLE_TYPE,
    DEFAULT_REPORT_FUEL_TYPE, DEFAULT_REPORT_FUEL_CONSUMPTION, DEFAULT_REPORT_ENGINE_CC, DEFAULT_REPORT_START_ODOMETER,
    DEFAULT_REPORT_HOME_LABEL, DEFAULT_REPORT_HOME_ADDRESS, DEFAULT_REPORT_FUEL_PRICE, DEFAULT_REPORT_TRIP_NATURE,
    DEFAULT_COMMUTE_ONE_WAY_KM, DEFAULT_REIMBURSEMENT_HUF_PER_KM,
    DEFAULT_EMAIL_RECIPIENT, DEFAULT_EMAIL_SUBJECT, DEFAULT_EMAIL_BODY,
    DOMAIN,
)


class UtnyilvantartasConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            client = AlapnyomkovetesClient(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            try:
                await client.async_login()
            except AlapnyomkovetesAuthError:
                errors["base"] = "invalid_auth"
            except AlapnyomkovetesError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            finally:
                await client.async_close()

            if not errors:
                unique = f"alapnyomkovetes-{user_input[CONF_DEVICE_ID]}"
                await self.async_set_unique_id(unique)
                self._abort_if_unique_id_configured()
                title = user_input.get(CONF_VEHICLE_NAME) or f"Útnyilvántartás {user_input[CONF_DEVICE_ID]}"
                return self.async_create_entry(title=title, data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_DEVICE_ID): vol.Coerce(int),
                vol.Optional(CONF_VEHICLE_NAME, default="Céges autó"): str,
                vol.Optional(CONF_HOME_ZONE, default=DEFAULT_HOME_ZONE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(CONF_WORK_ZONE, default=DEFAULT_WORK_ZONE): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(CONF_HOME_GPS_RADIUS, default=DEFAULT_HOME_GPS_RADIUS): vol.All(
                    vol.Coerce(float), vol.Range(min=50, max=2000)
                ),
                vol.Optional(CONF_WORK_GPS_RADIUS, default=DEFAULT_WORK_GPS_RADIUS): vol.All(
                    vol.Coerce(float), vol.Range(min=50, max=2000)
                ),
                vol.Optional(CONF_ENDPOINT_WINDOW_KM, default=DEFAULT_ENDPOINT_WINDOW_KM): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=5)
                ),
                vol.Optional(CONF_KELIO_ENTITY, default=DEFAULT_KELIO_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig()),
                vol.Optional(CONF_KELIO_MONTH_ENTITY, default=DEFAULT_KELIO_MONTH_ENTITY): selector.EntitySelector(selector.EntitySelectorConfig()),
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    vol.Coerce(int), vol.Range(min=5, max=120)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        # Home Assistant 2026+ injects the config entry into OptionsFlow and
        # exposes it through self.config_entry. The property is read-only now,
        # so it must not be assigned manually.
        return UtnyilvantartasOptionsFlow()


class UtnyilvantartasOptionsFlow(config_entries.OptionsFlow):
    def _current(self, key, default=None):
        return self.config_entry.options.get(key, self.config_entry.data.get(key, default))

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(CONF_HOME_ZONE, default=self._current(CONF_HOME_ZONE, DEFAULT_HOME_ZONE)):
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="zone")),
                vol.Optional(CONF_WORK_ZONE, default=self._current(CONF_WORK_ZONE, DEFAULT_WORK_ZONE)):
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="zone")),
                vol.Optional(CONF_HOME_GPS_RADIUS, default=self._current(CONF_HOME_GPS_RADIUS, DEFAULT_HOME_GPS_RADIUS)):
                    vol.All(vol.Coerce(float), vol.Range(min=50, max=2000)),
                vol.Optional(CONF_WORK_GPS_RADIUS, default=self._current(CONF_WORK_GPS_RADIUS, DEFAULT_WORK_GPS_RADIUS)):
                    vol.All(vol.Coerce(float), vol.Range(min=50, max=2000)),
                vol.Optional(CONF_ENDPOINT_WINDOW_KM, default=self._current(CONF_ENDPOINT_WINDOW_KM, DEFAULT_ENDPOINT_WINDOW_KM)):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=5)),
                vol.Optional(CONF_KELIO_ENTITY, default=self._current(CONF_KELIO_ENTITY, DEFAULT_KELIO_ENTITY)):
                    selector.EntitySelector(selector.EntitySelectorConfig()),
                vol.Optional(CONF_KELIO_MONTH_ENTITY, default=self._current(CONF_KELIO_MONTH_ENTITY, DEFAULT_KELIO_MONTH_ENTITY)):
                    selector.EntitySelector(selector.EntitySelectorConfig()),
                vol.Optional(CONF_SCAN_INTERVAL, default=self._current(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                    vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
                vol.Optional(CONF_REPORT_COMPANY_NAME, default=self._current(CONF_REPORT_COMPANY_NAME, DEFAULT_REPORT_COMPANY_NAME)): str,
                vol.Optional(CONF_REPORT_COMPANY_ADDRESS, default=self._current(CONF_REPORT_COMPANY_ADDRESS, DEFAULT_REPORT_COMPANY_ADDRESS)): str,
                vol.Optional(CONF_REPORT_TAX_NUMBER, default=self._current(CONF_REPORT_TAX_NUMBER, DEFAULT_REPORT_TAX_NUMBER)): str,
                vol.Optional(CONF_REPORT_EMPLOYEE_NAME, default=self._current(CONF_REPORT_EMPLOYEE_NAME, DEFAULT_REPORT_EMPLOYEE_NAME)): str,
                vol.Optional(CONF_REPORT_PRIVATE_VEHICLE, default=self._current(CONF_REPORT_PRIVATE_VEHICLE, DEFAULT_REPORT_PRIVATE_VEHICLE)): str,
                vol.Optional(CONF_REPORT_VEHICLE_TYPE, default=self._current(CONF_REPORT_VEHICLE_TYPE, DEFAULT_REPORT_VEHICLE_TYPE)): str,
                vol.Optional(CONF_REPORT_FUEL_TYPE, default=self._current(CONF_REPORT_FUEL_TYPE, DEFAULT_REPORT_FUEL_TYPE)): str,
                vol.Optional(CONF_REPORT_ENGINE_CC, default=self._current(CONF_REPORT_ENGINE_CC, DEFAULT_REPORT_ENGINE_CC)):
                    vol.All(vol.Coerce(int), vol.Range(min=0, max=20000)),
                vol.Optional(CONF_REPORT_FUEL_CONSUMPTION, default=self._current(CONF_REPORT_FUEL_CONSUMPTION, DEFAULT_REPORT_FUEL_CONSUMPTION)):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Optional(CONF_REPORT_START_ODOMETER, default=self._current(CONF_REPORT_START_ODOMETER, DEFAULT_REPORT_START_ODOMETER)):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=10000000)),
                vol.Optional(CONF_REPORT_HOME_LABEL, default=self._current(CONF_REPORT_HOME_LABEL, DEFAULT_REPORT_HOME_LABEL)): str,
                vol.Optional(CONF_REPORT_HOME_ADDRESS, default=self._current(CONF_REPORT_HOME_ADDRESS, DEFAULT_REPORT_HOME_ADDRESS)): str,
                vol.Optional(CONF_REPORT_FUEL_PRICE, default=self._current(CONF_REPORT_FUEL_PRICE, DEFAULT_REPORT_FUEL_PRICE)):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=10000)),
                vol.Optional(CONF_REPORT_TRIP_NATURE, default=self._current(CONF_REPORT_TRIP_NATURE, DEFAULT_REPORT_TRIP_NATURE)): str,
                vol.Optional(CONF_COMMUTE_ONE_WAY_KM, default=self._current(CONF_COMMUTE_ONE_WAY_KM, DEFAULT_COMMUTE_ONE_WAY_KM)):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=500)),
                vol.Optional(CONF_REIMBURSEMENT_HUF_PER_KM, default=self._current(CONF_REIMBURSEMENT_HUF_PER_KM, DEFAULT_REIMBURSEMENT_HUF_PER_KM)):
                    vol.All(vol.Coerce(float), vol.Range(min=0, max=1000)),
                vol.Optional(
                    CONF_EMAIL_RECIPIENT,
                    default=self._current(CONF_EMAIL_RECIPIENT, DEFAULT_EMAIL_RECIPIENT),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        autocomplete="email",
                    )
                ),
                vol.Optional(
                    CONF_EMAIL_SUBJECT,
                    default=self._current(CONF_EMAIL_SUBJECT, DEFAULT_EMAIL_SUBJECT),
                ): selector.TextSelector(
                    selector.TextSelectorConfig()
                ),
                vol.Optional(
                    CONF_EMAIL_BODY,
                    default=self._current(CONF_EMAIL_BODY, DEFAULT_EMAIL_BODY),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
