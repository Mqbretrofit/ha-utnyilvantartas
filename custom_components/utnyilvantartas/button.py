from __future__ import annotations

from pathlib import Path
import shutil

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import (
    CONF_COMMUTE_ONE_WAY_KM, CONF_DEVICE_ID, CONF_REIMBURSEMENT_HUF_PER_KM,
    CONF_REPORT_COMPANY_ADDRESS, CONF_REPORT_COMPANY_NAME, CONF_REPORT_EMPLOYEE_NAME,
    CONF_REPORT_PRIVATE_VEHICLE, CONF_REPORT_VEHICLE_TYPE, CONF_REPORT_FUEL_TYPE,
    CONF_REPORT_FUEL_CONSUMPTION, CONF_REPORT_ENGINE_CC, CONF_REPORT_START_ODOMETER, CONF_REPORT_HOME_LABEL,
    CONF_REPORT_HOME_ADDRESS, CONF_REPORT_FUEL_PRICE, CONF_REPORT_TRIP_NATURE,
    CONF_REPORT_TAX_NUMBER, CONF_VEHICLE_NAME, DOMAIN,
    CONF_EMAIL_RECIPIENT, CONF_EMAIL_SUBJECT, CONF_EMAIL_BODY,
    DEFAULT_COMMUTE_ONE_WAY_KM, DEFAULT_REIMBURSEMENT_HUF_PER_KM,
    DEFAULT_REPORT_COMPANY_ADDRESS, DEFAULT_REPORT_COMPANY_NAME, DEFAULT_REPORT_EMPLOYEE_NAME,
    DEFAULT_REPORT_PRIVATE_VEHICLE, DEFAULT_REPORT_VEHICLE_TYPE, DEFAULT_REPORT_FUEL_TYPE,
    DEFAULT_REPORT_FUEL_CONSUMPTION, DEFAULT_REPORT_ENGINE_CC, DEFAULT_REPORT_START_ODOMETER, DEFAULT_REPORT_HOME_LABEL,
    DEFAULT_REPORT_HOME_ADDRESS, DEFAULT_REPORT_FUEL_PRICE, DEFAULT_REPORT_TRIP_NATURE, DEFAULT_REPORT_TAX_NUMBER,
    DEFAULT_EMAIL_RECIPIENT, DEFAULT_EMAIL_SUBJECT, DEFAULT_EMAIL_BODY,
)
from .coordinator import UtnyMonthlyCoordinator
from .pdf_report import generate_monthly_pdf
from .nav_fuel import async_fetch_nav_fuel_price
from .nav_norm import async_fetch_nav_consumption_norm


def _entry_value(entry: ConfigEntry, key: str, default=None):
    return entry.options.get(key, entry.data.get(key, default))


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    device_id = int(entry.data[CONF_DEVICE_ID])
    return DeviceInfo(
        identifiers={(DOMAIN, str(device_id))},
        name=entry.data.get(CONF_VEHICLE_NAME, "Céges autó"),
        manufacturer="Alapnyomkövetés",
        model=f"GPS eszköz {device_id}",
    )


def _render_email_template(template: str, *, month: str, employee: str, company: str, filename: str) -> str:
    values = {
        "month": month,
        "employee": employee,
        "company": company,
        "filename": filename,
    }

    class _SafeDict(dict):
        def __missing__(self, key):
            return "{" + key + "}"

    try:
        return str(template or "").format_map(_SafeDict(values))
    except Exception:
        return str(template or "")


def _find_smtp_notify_entity(hass: HomeAssistant, recipient: str) -> str | None:
    """Resolve the SMTP notify entity created for the configured email recipient."""
    recipient_norm = recipient.strip().lower()
    if not recipient_norm:
        return None

    registry = er.async_get(hass)
    for smtp_entry in hass.config_entries.async_entries("smtp"):
        for subentry in smtp_entry.subentries.values():
            unique_email = str(subentry.unique_id or "").strip()
            if unique_email.lower() != recipient_norm:
                continue
            expected_unique_id = f"{smtp_entry.entry_id}_{unique_email}"
            for entity in er.async_entries_for_config_entry(registry, smtp_entry.entry_id):
                if entity.domain == "notify" and entity.unique_id == expected_unique_id:
                    return entity.entity_id
    return None



async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    monthly: UtnyMonthlyCoordinator = hass.data[DOMAIN][entry.entry_id]["monthly_coordinator"]
    async_add_entities([
        MonthlyRefreshButton(monthly, entry),
        MonthlyPdfButton(hass, monthly, entry),
        MonthlyEmailButton(hass, monthly, entry),
    ])


class MonthlyRefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "refresh_month"
    _attr_icon = "mdi:calendar-sync"

    def __init__(self, coordinator: UtnyMonthlyCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator; self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}-refresh-month"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        await self.coordinator.async_refresh_with_kelio_history()


class MonthlyPdfButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "generate_pdf"
    _attr_icon = "mdi:file-pdf-box"

    def __init__(self, hass: HomeAssistant, coordinator: UtnyMonthlyCoordinator, entry: ConfigEntry) -> None:
        self.hass = hass; self.coordinator = coordinator; self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}-generate-pdf"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        # A PDF mindig friss Kelio + GPS havi adatokból készüljön.
        await self.coordinator.async_refresh_with_kelio_history()
        data = self.coordinator.data
        if data is None or not data.source_available:
            raise HomeAssistantError("A havi adatok nem állnak rendelkezésre a PDF generálásához.")

        month = data.month
        out_dir = Path(self.hass.config.path("www", "utnyilvantartas"))
        path = out_dir / f"utnyilvantartas_{month}.pdf"
        fuel_type = _entry_value(self.entry, CONF_REPORT_FUEL_TYPE, DEFAULT_REPORT_FUEL_TYPE)
        fallback_fuel_price = float(
            _entry_value(self.entry, CONF_REPORT_FUEL_PRICE, DEFAULT_REPORT_FUEL_PRICE)
        )
        fuel_price = fallback_fuel_price
        fuel_price_source = "Kézi tartalék"
        fuel_price_source_url = None
        try:
            nav_price = await async_fetch_nav_fuel_price(
                async_get_clientsession(self.hass),
                month_value=month,
                fuel_type=fuel_type,
            )
            fuel_price = float(nav_price.price_huf)
            fuel_price_source = "NAV"
            fuel_price_source_url = nav_price.source_url
        except Exception as err:  # noqa: BLE001
            # A PDF ettől még elkészül a beállított tartalékárral.
            fuel_price_source = f"Kézi tartalék (NAV hiba: {type(err).__name__})"

        vehicle_type = _entry_value(self.entry, CONF_REPORT_VEHICLE_TYPE, DEFAULT_REPORT_VEHICLE_TYPE)
        engine_cc = int(_entry_value(self.entry, CONF_REPORT_ENGINE_CC, DEFAULT_REPORT_ENGINE_CC) or 0)
        fallback_consumption = float(
            _entry_value(self.entry, CONF_REPORT_FUEL_CONSUMPTION, DEFAULT_REPORT_FUEL_CONSUMPTION)
        )
        fuel_consumption = fallback_consumption
        fuel_consumption_source = "Kézi tartalék"
        fuel_consumption_source_url = None

        if engine_cc > 0:
            try:
                nav_norm = await async_fetch_nav_consumption_norm(
                    async_get_clientsession(self.hass),
                    fuel_type=fuel_type,
                    engine_cc=engine_cc,
                )
                fuel_consumption = float(nav_norm.consumption)
                fuel_consumption_source = "NAV alapnorma-átalány"
                fuel_consumption_source_url = nav_norm.source_url
            except Exception as err:  # noqa: BLE001
                fuel_consumption_source = f"Kézi tartalék (NAV norma hiba: {type(err).__name__})"

        meta = {
            "company_name": _entry_value(self.entry, CONF_REPORT_COMPANY_NAME, DEFAULT_REPORT_COMPANY_NAME),
            "company_address": _entry_value(self.entry, CONF_REPORT_COMPANY_ADDRESS, DEFAULT_REPORT_COMPANY_ADDRESS),
            "tax_number": _entry_value(self.entry, CONF_REPORT_TAX_NUMBER, DEFAULT_REPORT_TAX_NUMBER),
            "employee_name": _entry_value(self.entry, CONF_REPORT_EMPLOYEE_NAME, DEFAULT_REPORT_EMPLOYEE_NAME),
            "private_vehicle": _entry_value(self.entry, CONF_REPORT_PRIVATE_VEHICLE, DEFAULT_REPORT_PRIVATE_VEHICLE),
            "vehicle_type": vehicle_type,
            "engine_cc": engine_cc,
            "fuel_type": fuel_type,
            "fuel_consumption_l_100km": fuel_consumption,
            "fuel_consumption_source": fuel_consumption_source,
            "fuel_consumption_source_url": fuel_consumption_source_url,
            "start_odometer_km": data.odometer_start_km,
            "end_odometer_km": data.odometer_end_km,
            "home_label": _entry_value(self.entry, CONF_REPORT_HOME_LABEL, DEFAULT_REPORT_HOME_LABEL),
            "home_address": _entry_value(self.entry, CONF_REPORT_HOME_ADDRESS, DEFAULT_REPORT_HOME_ADDRESS),
            "fuel_price_huf_l": fuel_price,
            "fuel_price_source": fuel_price_source,
            "fuel_price_source_url": fuel_price_source_url,
            "trip_nature": _entry_value(self.entry, CONF_REPORT_TRIP_NATURE, DEFAULT_REPORT_TRIP_NATURE),
            "company_vehicle": self.entry.data.get(CONF_VEHICLE_NAME, "Céges autó"),
            "commute_one_way_km": float(_entry_value(self.entry, CONF_COMMUTE_ONE_WAY_KM, DEFAULT_COMMUTE_ONE_WAY_KM)),
            "reimbursement_huf_per_km": float(_entry_value(self.entry, CONF_REIMBURSEMENT_HUF_PER_KM, DEFAULT_REIMBURSEMENT_HUF_PER_KM)),
        }
        payload = {
            "month": data.month, "updated_at": data.updated_at,
            "presence_days": data.presence_days, "eligible_days": data.eligible_days,
            "ineligible_presence_days": data.ineligible_presence_days,
            "eligible_legs": data.eligible_legs,
            "ineligible_legs": data.ineligible_legs,
            "unknown_legs": data.unknown_legs,
            "company_distance_km": data.company_distance_km,
            "private_commute_km": data.private_commute_km,
            "reimbursement_huf": data.reimbursement_huf,
            "odometer_start_km": data.odometer_start_km,
            "odometer_end_km": data.odometer_end_km,
            "records": data.records,
        }
        try:
            await self.hass.async_add_executor_job(generate_monthly_pdf, path, payload, meta)
            stamp = int(dt_util.utcnow().timestamp())
            url = f"/local/utnyilvantartas/{path.name}?v={stamp}"
            await self.coordinator.async_refresh_saved_pdfs()
            self.coordinator.set_pdf_result(path=str(path), url=url, month=month, error=None)
        except Exception as err:  # noqa: BLE001
            self.coordinator.set_pdf_result(path=None, url=None, month=month, error=str(err))
            raise HomeAssistantError(f"PDF generálási hiba: {err}") from err


class MonthlyEmailButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "email_pdf"
    _attr_icon = "mdi:email-fast-outline"

    def __init__(self, hass: HomeAssistant, coordinator: UtnyMonthlyCoordinator, entry: ConfigEntry) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}-email-pdf"
        self._attr_device_info = _device_info(entry)

    async def async_press(self) -> None:
        recipient = str(
            _entry_value(self.entry, CONF_EMAIL_RECIPIENT, DEFAULT_EMAIL_RECIPIENT) or ""
        ).strip()
        if not recipient:
            raise HomeAssistantError(
                "Nincs megadva e-mail cím. Állítsd be az Útnyilvántartás beállításaiban."
            )

        if not self.hass.services.has_service("smtp", "send_message"):
            raise HomeAssistantError(
                "A Home Assistant SMTP integráció nincs beállítva. "
                "Beállítások → Eszközök és szolgáltatások → Integráció hozzáadása → SMTP."
            )

        notify_entity = _find_smtp_notify_entity(self.hass, recipient)
        if not notify_entity:
            raise HomeAssistantError(
                f"Az SMTP integrációban nincs ilyen címzett: {recipient}. "
                "Az SMTP integrációnál add hozzá ezt a címet címzettként."
            )

        # Mindig friss PDF menjen ki, ugyanazzal a logikával, mint a PDF gombnál.
        pdf_button = MonthlyPdfButton(self.hass, self.coordinator, self.entry)
        await pdf_button.async_press()

        pdf_path_value = self.coordinator.last_pdf_path
        month = self.coordinator.last_pdf_month
        if not pdf_path_value or not month:
            raise HomeAssistantError("A PDF elkészült fájlútvonala nem érhető el.")

        pdf_path = Path(pdf_path_value)
        if not pdf_path.exists():
            raise HomeAssistantError(f"A PDF fájl nem található: {pdf_path}")

        # SMTP 2026 attachment action media_source fájlt vár.
        #
        # FONTOS: a local media_source alapértelmezett fizikai könyvtára HAOS-on
        # /media, NEM /config/media. A korábbi verzió a /config/media alá másolt,
        # miközben a media-source URI /media/... útvonalat próbált megnyitni.
        media_dirs = dict(self.hass.config.media_dirs or {})
        media_root_value = media_dirs.get("local")
        if not media_root_value and media_dirs:
            media_root_value = next(iter(media_dirs.values()))
        if not media_root_value:
            media_root_value = "/media"

        media_root = Path(str(media_root_value))
        media_dir = media_root / "utnyilvantartas"
        media_path = media_dir / pdf_path.name

        def _copy_pdf() -> None:
            media_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(pdf_path, media_path)
            if not media_path.exists():
                raise FileNotFoundError(str(media_path))

        await self.hass.async_add_executor_job(_copy_pdf)

        employee = str(
            _entry_value(self.entry, CONF_REPORT_EMPLOYEE_NAME, DEFAULT_REPORT_EMPLOYEE_NAME)
        )
        company = str(
            _entry_value(self.entry, CONF_REPORT_COMPANY_NAME, DEFAULT_REPORT_COMPANY_NAME)
        )
        subject_template = str(
            _entry_value(self.entry, CONF_EMAIL_SUBJECT, DEFAULT_EMAIL_SUBJECT)
        )
        body_template = str(
            _entry_value(self.entry, CONF_EMAIL_BODY, DEFAULT_EMAIL_BODY)
        )
        subject = _render_email_template(
            subject_template,
            month=month,
            employee=employee,
            company=company,
            filename=pdf_path.name,
        )
        body = _render_email_template(
            body_template,
            month=month,
            employee=employee,
            company=company,
            filename=pdf_path.name,
        )

        if not media_path.exists():
            raise HomeAssistantError(
                f"A csatolmány nem található a Home Assistant média könyvtárában: {media_path}"
            )

        media_content_id = (
            f"media-source://media_source/local/utnyilvantartas/{media_path.name}"
        )
        try:
            await self.hass.services.async_call(
                "smtp",
                "send_message",
                {
                    "entity_id": notify_entity,
                    "title": subject,
                    "message": body,
                    "attachments": [
                        {
                            "media_source": {
                                "media_content_id": media_content_id,
                                "media_content_type": "application/pdf",
                            },
                            "filename": pdf_path.name,
                        }
                    ],
                },
                blocking=True,
            )
        except Exception as err:  # noqa: BLE001
            raise HomeAssistantError(f"E-mail küldési hiba: {err}") from err

