"""Frontend registration for Útnyilvántartás."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).parent
FRONTEND_BASE = "/utnyilvantartas_static"
CARD_FILENAME = "utnyilvantartas-card.js"
CARD_URL = f"{FRONTEND_BASE}/{CARD_FILENAME}?v=0.4.44"
PANEL_PATH = "utnyilvantartas"
PANEL_ELEMENT = "utnyilvantartas-card"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the JS and register the sidebar panel."""
    card_path = FRONTEND_DIR / CARD_FILENAME
    if not card_path.exists():
        raise RuntimeError(f"Útnyilvántartás frontend fájl nem található: {card_path}")

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_BASE, str(FRONTEND_DIR), False)]
        )
    except RuntimeError:
        pass

    try:
        frontend.add_extra_js_url(hass, CARD_URL)
    except Exception as err:
        _LOGGER.debug("Útnyilvántartás extra JS regisztráció kihagyva: %s", err)

    if frontend.async_panel_exists(hass, PANEL_PATH):
        frontend.async_remove_panel(hass, PANEL_PATH)

    frontend.async_register_built_in_panel(
        hass,
        component_name="custom",
        sidebar_title="Útnyilvántartás",
        sidebar_icon="mdi:car-clock",
        frontend_url_path=PANEL_PATH,
        config={
            "integration": "utnyilvantartas",
            "version": "0.4.44",
            "_panel_custom": {
                "name": PANEL_ELEMENT,
                "embed_iframe": False,
                "trust_external": False,
                "handle_safe_area": False,
                "module_url": CARD_URL,
            },
        },
        require_admin=False,
        show_in_sidebar=True,
    )

    if not frontend.async_panel_exists(hass, PANEL_PATH):
        raise RuntimeError(
            "Az Útnyilvántartás panel nem került be a frontend paneltérképbe."
        )

    _LOGGER.warning(
        "Útnyilvántartás frontend OK: panel=/%s module=%s",
        PANEL_PATH,
        CARD_URL,
    )


def async_unregister_frontend(hass: HomeAssistant) -> None:
    if frontend.async_panel_exists(hass, PANEL_PATH):
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
