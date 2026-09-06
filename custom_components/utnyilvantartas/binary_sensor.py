from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_REASON,
    CONF_DEVICE_ID,
    CONF_KELIO_ENTITY,
    CONF_VEHICLE_NAME,
    DEFAULT_KELIO_ENTITY,
    DOMAIN,
)
from .coordinator import DailySummary, UtnyilvantartasCoordinator


@dataclass(frozen=True, kw_only=True)
class UtnyBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[DailySummary], bool | None]


BINARY_SENSORS = (
    UtnyBinaryDescription(
        key="movement_detected",
        translation_key="movement_detected",
        icon="mdi:car-arrow-right",
        value_fn=lambda d: d.movement_detected,
    ),
    UtnyBinaryDescription(
        key="started_home",
        translation_key="started_home",
        icon="mdi:home-export-outline",
        value_fn=lambda d: d.started_home,
    ),
    UtnyBinaryDescription(
        key="ended_home",
        translation_key="ended_home",
        icon="mdi:home-import-outline",
        value_fn=lambda d: d.ended_home,
    ),
    UtnyBinaryDescription(
        key="touched_home",
        translation_key="touched_home",
        icon="mdi:home-map-marker",
        value_fn=lambda d: d.touched_home,
    ),
    UtnyBinaryDescription(
        key="started_work",
        translation_key="started_work",
        icon="mdi:office-building-marker-outline",
        value_fn=lambda d: d.started_work,
    ),
    UtnyBinaryDescription(
        key="ended_work",
        translation_key="ended_work",
        icon="mdi:office-building-marker",
        value_fn=lambda d: d.ended_work,
    ),
    UtnyBinaryDescription(
        key="touched_work",
        translation_key="touched_work",
        icon="mdi:office-building",
        value_fn=lambda d: d.touched_work,
    ),
)


def _kelio_present(hass: HomeAssistant, entity_id: str | None) -> bool | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    value = state.state.strip().lower()
    if value in {"on", "true", "yes", "igen", "success", "already", "present", "jelen"}:
        return True
    if value in {"off", "false", "no", "nem", "absent", "nincs"}:
        return False
    return None


def _kelio_entity(entry: ConfigEntry) -> str:
    return str(
        entry.options.get(
            CONF_KELIO_ENTITY,
            entry.data.get(CONF_KELIO_ENTITY, DEFAULT_KELIO_ENTITY),
        )
        or DEFAULT_KELIO_ENTITY
    )


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    device_id = int(entry.data[CONF_DEVICE_ID])
    return DeviceInfo(
        identifiers={(DOMAIN, str(device_id))},
        name=entry.data.get(CONF_VEHICLE_NAME, "Céges autó"),
        manufacturer="Alapnyomkövetés",
        model=f"GPS eszköz {device_id}",
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    coordinator: UtnyilvantartasCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    entities = [UtnyBinarySensor(coordinator, entry, description) for description in BINARY_SENSORS]
    entities.append(KelioPresenceBinarySensor(coordinator, entry))
    entities.append(CommuteEligibilityBinarySensor(coordinator, entry))
    async_add_entities(entities)


class UtnyBinarySensor(CoordinatorEntity[UtnyilvantartasCoordinator], BinarySensorEntity):
    entity_description: UtnyBinaryDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}-{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)


class _KelioListeningEntity:
    """Mixin for entities that mirror the Kelio helper entity live."""

    kelio_entity: str

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        remove_listener = async_track_state_change_event(
            self.hass,
            [self.kelio_entity],
            self._async_kelio_changed,
        )
        self.async_on_remove(remove_listener)

    @callback
    def _async_kelio_changed(self, event) -> None:
        self.async_write_ha_state()


class KelioPresenceBinarySensor(
    _KelioListeningEntity,
    CoordinatorEntity[UtnyilvantartasCoordinator],
    BinarySensorEntity,
):
    _attr_has_entity_name = True
    _attr_translation_key = "kelio_presence_today"
    _attr_icon = "mdi:account-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.kelio_entity = _kelio_entity(entry)
        self._attr_unique_id = f"{entry.entry_id}-kelio-presence-today"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool | None:
        return _kelio_present(self.hass, self.kelio_entity)

    @property
    def extra_state_attributes(self):
        source = self.hass.states.get(self.kelio_entity)
        return {
            "source_entity": self.kelio_entity,
            "source_state": source.state if source else None,
            "source_available": source is not None and source.state not in ("unknown", "unavailable"),
        }


class CommuteEligibilityBinarySensor(
    _KelioListeningEntity,
    CoordinatorEntity[UtnyilvantartasCoordinator],
    BinarySensorEntity,
):
    _attr_has_entity_name = True
    _attr_translation_key = "commute_eligible"
    _attr_icon = "mdi:cash-check"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.kelio_entity = _kelio_entity(entry)
        self._attr_unique_id = f"{entry.entry_id}-commute-eligible"
        self._attr_device_info = _device_info(entry)

    def _result(self) -> tuple[bool | None, str]:
        kelio = _kelio_present(self.hass, self.kelio_entity)
        if kelio is None:
            return None, f"Kelio jelenlét nem állapítható meg ({self.kelio_entity})"
        if not kelio:
            return False, "Kelio szerint nincs igazolt munkanapi jelenlét"

        runtime = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id, {})
        monthly = runtime.get("monthly_coordinator")
        today_key = dt_util.now().date().isoformat()

        if monthly is not None and monthly.data is not None:
            record = next(
                (
                    item
                    for item in monthly.data.records
                    if str(item.get("date") or "") == today_key
                ),
                None,
            )
            if record is not None:
                morning = record.get("morning_commute_eligible")
                evening = record.get("evening_commute_eligible")
                eligible_legs = int(record.get("eligible_legs") or 0)
                unknown_legs = int(record.get("unknown_legs") or 0)

                if eligible_legs > 0:
                    return True, (
                        f"Ma {eligible_legs}/2 saját autós bejárási út jár "
                        f"(reggel={morning}, este={evening})"
                    )
                if unknown_legs > 0:
                    return None, (
                        f"A mai bejárás még nem teljesen eldönthető "
                        f"(reggel={morning}, este={evening})"
                    )
                return False, "Ma egyik saját autós bejárási út sem jár"

        return None, "A mai két bejárási út havi/éjszakai kiértékelése még nem készült el"

    @property
    def is_on(self) -> bool | None:
        return self._result()[0]

    @property
    def extra_state_attributes(self):
        value, reason = self._result()
        data = self.coordinator.data
        kelio = _kelio_present(self.hass, self.kelio_entity)
        source = self.hass.states.get(self.kelio_entity)
        return {
            ATTR_REASON: reason,
            "kelio_entity": self.kelio_entity,
            "kelio_presence_today": kelio,
            "kelio_source_state": source.state if source else None,
            "started_home": data.started_home,
            "ended_home": data.ended_home,
            "started_work": data.started_work,
            "ended_work": data.ended_work,
            "daily_distance_km": data.distance_km,
            "movement_detected": data.movement_detected,
            "home_gps_radius_m": data.home_radius_m,
            "work_gps_radius_m": data.work_radius_m,
            "endpoint_window_km": data.endpoint_window_km,
            "start_home_min_distance_m": data.start_home_min_distance_m,
            "end_home_min_distance_m": data.end_home_min_distance_m,
            "start_work_min_distance_m": data.start_work_min_distance_m,
            "end_work_min_distance_m": data.end_work_min_distance_m,
        }
