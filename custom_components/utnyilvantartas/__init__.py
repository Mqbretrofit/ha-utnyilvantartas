from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event

from .api import AlapnyomkovetesClient
from .const import (
    CONF_DEVICE_ID,
    CONF_HOME_ZONE,
    CONF_HOME_GPS_RADIUS,
    CONF_KELIO_ENTITY,
    CONF_KELIO_MONTH_ENTITY,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_ENDPOINT_WINDOW_KM,
    CONF_USERNAME,
    CONF_WORK_ZONE,
    CONF_WORK_GPS_RADIUS,
    CONF_COMMUTE_ONE_WAY_KM,
    CONF_REIMBURSEMENT_HUF_PER_KM,
    CONF_REPORT_START_ODOMETER,
    DEFAULT_HOME_ZONE,
    DEFAULT_HOME_GPS_RADIUS,
    DEFAULT_KELIO_ENTITY,
    DEFAULT_KELIO_MONTH_ENTITY,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_ENDPOINT_WINDOW_KM,
    DEFAULT_WORK_ZONE,
    DEFAULT_WORK_GPS_RADIUS,
    DEFAULT_COMMUTE_ONE_WAY_KM,
    DEFAULT_REIMBURSEMENT_HUF_PER_KM,
    DEFAULT_REPORT_START_ODOMETER,
    DOMAIN,
    PLATFORMS,
)
from .coordinator import UtnyilvantartasCoordinator, UtnyMonthlyCoordinator
from .frontend import async_register_frontend

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_MONTH = "set_month"
SERVICE_SET_MANUAL_DAY = "set_manual_day"
SERVICE_REMOVE_MANUAL_DAY = "remove_manual_day"


def _runtime_for_service(hass: HomeAssistant, entry_id: str | None):
    domain_data = hass.data.get(DOMAIN, {})
    if entry_id:
        runtime = domain_data.get(entry_id)
        if runtime is None:
            raise HomeAssistantError(f"Ismeretlen Útnyilvántartás config entry: {entry_id}")
        return runtime
    runtimes = list(domain_data.values())
    if len(runtimes) == 1:
        return runtimes[0]
    raise HomeAssistantError(
        "Több Útnyilvántartás példány van. A config entry azonosító megadása szükséges."
    )


async def _async_handle_set_month(hass: HomeAssistant, call: ServiceCall) -> None:
    month = str(call.data.get("month") or "").strip()
    entry_id_raw = call.data.get("entry_id")
    entry_id = str(entry_id_raw).strip() if entry_id_raw else None
    runtime = _runtime_for_service(hass, entry_id)
    monthly: UtnyMonthlyCoordinator = runtime["monthly_coordinator"]
    await monthly.async_refresh_with_kelio_history(target_month=month)


async def _async_handle_set_manual_day(hass: HomeAssistant, call: ServiceCall) -> None:
    day = str(call.data.get("date") or "").strip()
    morning = bool(call.data.get("morning_eligible", True))
    evening = bool(call.data.get("evening_eligible", True))
    note = str(call.data.get("note") or "").strip()
    entry_id_raw = call.data.get("entry_id")
    entry_id = str(entry_id_raw).strip() if entry_id_raw else None
    runtime = _runtime_for_service(hass, entry_id)
    monthly: UtnyMonthlyCoordinator = runtime["monthly_coordinator"]
    await monthly.async_set_manual_day(
        day,
        morning_eligible=morning,
        evening_eligible=evening,
        note=note,
    )


async def _async_handle_remove_manual_day(hass: HomeAssistant, call: ServiceCall) -> None:
    day = str(call.data.get("date") or "").strip()
    entry_id_raw = call.data.get("entry_id")
    entry_id = str(entry_id_raw).strip() if entry_id_raw else None
    runtime = _runtime_for_service(hass, entry_id)
    monthly: UtnyMonthlyCoordinator = runtime["monthly_coordinator"]
    await monthly.async_remove_manual_day(day)


def _entry_value(entry: ConfigEntry, key: str, default=None):
    return entry.options.get(key, entry.data.get(key, default))


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up integration-wide frontend resources and services."""
    try:
        await async_register_frontend(hass)
    except Exception:
        _LOGGER.exception("Útnyilvántartás frontend korai regisztráció sikertelen")

    if not hass.services.has_service(DOMAIN, SERVICE_SET_MONTH):
        async def _set_month_service(call: ServiceCall) -> None:
            await _async_handle_set_month(hass, call)

        hass.services.async_register(DOMAIN, SERVICE_SET_MONTH, _set_month_service)

    if not hass.services.has_service(DOMAIN, SERVICE_SET_MANUAL_DAY):
        async def _set_manual_day_service(call: ServiceCall) -> None:
            await _async_handle_set_manual_day(hass, call)

        hass.services.async_register(DOMAIN, SERVICE_SET_MANUAL_DAY, _set_manual_day_service)

    if not hass.services.has_service(DOMAIN, SERVICE_REMOVE_MANUAL_DAY):
        async def _remove_manual_day_service(call: ServiceCall) -> None:
            await _async_handle_remove_manual_day(hass, call)

        hass.services.async_register(DOMAIN, SERVICE_REMOVE_MANUAL_DAY, _remove_manual_day_service)

    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        await async_register_frontend(hass)
    except Exception:
        _LOGGER.exception(
            "Útnyilvántartás frontend regisztráció sikertelen a config entry betöltésekor"
        )
    client = AlapnyomkovetesClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
    )
    coordinator = UtnyilvantartasCoordinator(
        hass,
        client,
        int(entry.data[CONF_DEVICE_ID]),
        _entry_value(entry, CONF_HOME_ZONE, DEFAULT_HOME_ZONE),
        _entry_value(entry, CONF_WORK_ZONE, DEFAULT_WORK_ZONE),
        float(_entry_value(entry, CONF_HOME_GPS_RADIUS, DEFAULT_HOME_GPS_RADIUS)),
        float(_entry_value(entry, CONF_WORK_GPS_RADIUS, DEFAULT_WORK_GPS_RADIUS)),
        float(_entry_value(entry, CONF_ENDPOINT_WINDOW_KM, DEFAULT_ENDPOINT_WINDOW_KM)),
        timedelta(minutes=int(_entry_value(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))),
    )
    await coordinator.async_config_entry_first_refresh()

    kelio_today = str(_entry_value(entry, CONF_KELIO_ENTITY, DEFAULT_KELIO_ENTITY))
    kelio_month = str(_entry_value(entry, CONF_KELIO_MONTH_ENTITY, DEFAULT_KELIO_MONTH_ENTITY))
    monthly = UtnyMonthlyCoordinator(
        hass,
        coordinator,
        kelio_month,
        kelio_today,
        float(_entry_value(entry, CONF_COMMUTE_ONE_WAY_KM, DEFAULT_COMMUTE_ONE_WAY_KM)),
        float(_entry_value(entry, CONF_REIMBURSEMENT_HUF_PER_KM, DEFAULT_REIMBURSEMENT_HUF_PER_KM)),
        float(_entry_value(entry, CONF_REPORT_START_ODOMETER, DEFAULT_REPORT_START_ODOMETER)),
    )
    await monthly.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "monthly_coordinator": monthly,
    }

    @callback
    def _kelio_changed(event) -> None:
        # A kézi havi újraszámítás maga megvárja a Kelio history_month végét,
        # ezért közben ne induljon párhuzamos GPS havi újraszámítás.
        if monthly.kelio_refresh_in_progress:
            return
        hass.async_create_task(monthly.async_request_refresh())

    remove_listener = async_track_state_change_event(
        hass,
        [kelio_month, kelio_today],
        _kelio_changed,
    )
    entry.async_on_unload(remove_listener)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = hass.data[DOMAIN].pop(entry.entry_id)
        await runtime["client"].async_close()
    return unloaded
