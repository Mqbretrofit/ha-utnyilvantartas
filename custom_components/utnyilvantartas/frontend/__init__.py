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
CORE_FILENAME = "utnyilvantartas-card.js"
PANEL_FILENAME = "utnyilvantartas-panel.js"
CORE_URL = f"{FRONTEND_BASE}/{CORE_FILENAME}?v=0.4.46"
PANEL_URL = f"{FRONTEND_BASE}/{PANEL_FILENAME}?v=0.4.46"
PANEL_PATH = "utnyilvantartas"
PANEL_ELEMENT = "utnyilvantartas-card"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the JS and register the sidebar panel."""
    core_path = FRONTEND_DIR / CORE_FILENAME
    panel_path = FRONTEND_DIR / PANEL_FILENAME
    if not core_path.exists():
        raise RuntimeError(f"Útnyilvántartás frontend fájl nem található: {core_path}")
    if not panel_path.exists():
        raise RuntimeError(f"Útnyilvántartás panel fájl nem található: {panel_path}")

    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(FRONTEND_BASE, str(FRONTEND_DIR), False)]
        )
    except RuntimeError:
        pass

    # Keep the proven v0.4.45 card implementation as the core element. The
    # panel module loaded below only adds multi-car selection on top of it.
    try:
        frontend.add_extra_js_url(hass, CORE_URL)
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
            "version": "0.4.46",
            "_panel_custom": {
                "name": PANEL_ELEMENT,
                "embed_iframe": False,
                "trust_external": False,
                "handle_safe_area": False,
                "module_url": PANEL_URL,
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
        "Útnyilvántartás frontend OK: panel=/%s module=%s core=%s",
        PANEL_PATH,
        PANEL_URL,
        CORE_URL,
    )


def async_unregister_frontend(hass: HomeAssistant) -> None:
    if frontend.async_panel_exists(hass, PANEL_PATH):
        frontend.async_remove_panel(hass, PANEL_PATH, warn_if_unknown=False)
