from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfLength, UnitOfSpeed
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTR_FIRST_POINT,
    ATTR_FIRST_TIME,
    ATTR_LAST_POINT,
    ATTR_LAST_TIME,
    ATTR_TOTAL_RECORDS,
    ATTR_UNIQUE_POINTS,
    ATTR_VALID_POINTS,
    CONF_DEVICE_ID,
    CONF_VEHICLE_NAME,
    DOMAIN,
)
from .coordinator import DailySummary, UtnyilvantartasCoordinator, UtnyMonthlyCoordinator


@dataclass(frozen=True, kw_only=True)
class UtnySensorDescription(SensorEntityDescription):
    value_fn: Callable[[DailySummary], Any]


SENSORS = (
    UtnySensorDescription(
        key="daily_distance",
        translation_key="daily_distance",
        native_unit_of_measurement=UnitOfLength.KILOMETERS,
        icon="mdi:map-marker-distance",
        value_fn=lambda d: d.distance_km,
    ),
    UtnySensorDescription(
        key="max_speed",
        translation_key="max_speed",
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        icon="mdi:speedometer",
        value_fn=lambda d: d.max_speed_kmh,
    ),
    UtnySensorDescription(
        key="first_movement",
        translation_key="first_movement",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:car-clock",
        value_fn=lambda d: d.first_point.when if d.first_point else None,
    ),
    UtnySensorDescription(
        key="last_movement",
        translation_key="last_movement",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:car-clock",
        value_fn=lambda d: d.last_point.when if d.last_point else None,
    ),
    UtnySensorDescription(
        key="gps_points",
        translation_key="gps_points",
        icon="mdi:map-marker-multiple",
        value_fn=lambda d: d.unique_points,
    ),
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
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: UtnyilvantartasCoordinator = runtime["coordinator"]
    monthly: UtnyMonthlyCoordinator = runtime["monthly_coordinator"]
    entities = [UtnySensor(coordinator, entry, description) for description in SENSORS]
    entities.append(MonthlySummarySensor(monthly, entry))
    entities.append(MonthlyPdfSensor(monthly, entry))
    async_add_entities(entities)


class UtnySensor(CoordinatorEntity[UtnyilvantartasCoordinator], SensorEntity):
    entity_description: UtnySensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}-{description.key}"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        attrs = {
            ATTR_TOTAL_RECORDS: data.total_records,
            ATTR_VALID_POINTS: data.valid_points,
            ATTR_UNIQUE_POINTS: data.unique_points,
            "home_gps_radius_m": data.home_radius_m,
            "work_gps_radius_m": data.work_radius_m,
            "endpoint_window_km": data.endpoint_window_km,
            "start_home_min_distance_m": data.start_home_min_distance_m,
            "end_home_min_distance_m": data.end_home_min_distance_m,
            "start_work_min_distance_m": data.start_work_min_distance_m,
            "end_work_min_distance_m": data.end_work_min_distance_m,
        }
        if data.first_point:
            attrs[ATTR_FIRST_TIME] = data.first_point.when.isoformat()
            attrs[ATTR_FIRST_POINT] = {
                "latitude": data.first_point.lat,
                "longitude": data.first_point.lon,
            }
        if data.last_point:
            attrs[ATTR_LAST_TIME] = data.last_point.when.isoformat()
            attrs[ATTR_LAST_POINT] = {
                "latitude": data.last_point.lat,
                "longitude": data.last_point.lon,
            }
        return attrs


class MonthlySummarySensor(CoordinatorEntity[UtnyMonthlyCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "monthly_summary"
    _attr_icon = "mdi:calendar-check-outline"
    _attr_native_unit_of_measurement = "nap"

    def __init__(self, coordinator: UtnyMonthlyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}-monthly-summary"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self):
        return self.coordinator.data.eligible_days

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data.source_available

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data
        return {
            "month": data.month,
            "selected_month": self.coordinator.selected_month,
            "config_entry_id": self.entry.entry_id,
            "kelio_presence_dates": data.presence_dates,
            "manual_presence_dates": data.manual_dates,
            "effective_presence_dates": sorted(set(data.presence_dates) | set(data.manual_dates)),
            "manual_presence_days": len(data.manual_dates),
            "kelio_refreshed_before_calculation": self.coordinator.last_kelio_refresh_at,
            "kelio_refreshed_month": self.coordinator.last_kelio_refresh_month,
            "presence_days": data.presence_days,
            "eligible_days": data.eligible_days,
            "ineligible_presence_days": data.ineligible_presence_days,
            "eligible_legs": data.eligible_legs,
            "ineligible_legs": data.ineligible_legs,
            "unknown_legs": data.unknown_legs,
            "accounting_mode": "commute_legs",
            "company_distance_km": data.company_distance_km,
            "private_commute_km": data.private_commute_km,
            "reimbursement_huf": data.reimbursement_huf,
            "commute_one_way_km": self.coordinator.commute_one_way_km,
            "reimbursement_huf_per_km": self.coordinator.reimbursement_huf_per_km,
            "odometer_start_km": data.odometer_start_km,
            "odometer_end_km": data.odometer_end_km,
            "home_gps_radius_m": self.coordinator.daily.home_gps_radius_m,
            "work_gps_radius_m": self.coordinator.daily.work_gps_radius_m,
            "endpoint_window_km": self.coordinator.daily.endpoint_window_km,
            "errors": data.errors,
            "updated_at": data.updated_at,
            "stored_file": data.stored_file,
            "records": data.records,
        }


class MonthlyPdfSensor(CoordinatorEntity[UtnyMonthlyCoordinator], SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "monthly_pdf"
    _attr_icon = "mdi:file-pdf-box"

    def __init__(self, coordinator: UtnyMonthlyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}-monthly-pdf"
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self):
        return self.coordinator.last_pdf_month or "nincs"

    @property
    def extra_state_attributes(self):
        return {
            "url": self.coordinator.last_pdf_url,
            "file": self.coordinator.last_pdf_path,
            "generated_at": self.coordinator.last_pdf_generated_at,
            "error": self.coordinator.last_pdf_error,
            "saved_pdfs": self.coordinator.saved_pdfs,
            "saved_pdf_count": len(self.coordinator.saved_pdfs),
            "pdf_directory": "/config/www/utnyilvantartas",
            "odometer_start_km": self.coordinator.data.odometer_start_km if self.coordinator.data else None,
            "odometer_end_km": self.coordinator.data.odometer_end_km if self.coordinator.data else None,
            "private_commute_km": self.coordinator.data.private_commute_km if self.coordinator.data else None,
            "reimbursement_huf": self.coordinator.data.reimbursement_huf if self.coordinator.data else None,
        }
