"""Admin-only native HA sidebar panel; no extra container or HA YAML needed."""
from __future__ import annotations

from pathlib import Path
import logging

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION, NAME, DEFAULT_ICON
from .websocket import register_commands, PANEL_URL

_LOGGER = logging.getLogger(__name__)
_FILE_URL = f"/{DOMAIN}_assets"


async def async_register_editor(hass: HomeAssistant) -> None:
    """Register backend and local frontend assets once per HA process."""
    state = hass.data.setdefault(DOMAIN + "_editor", {})
    if state.get("registered"):
        return
    if not state.get("assets_registered"):
        assets = str(Path(__file__).parent / "frontend")
        await hass.http.async_register_static_paths([
            StaticPathConfig(_FILE_URL, assets, cache_headers=False),
        ])
        state["assets_registered"] = True
    if not state.get("commands_registered"):
        register_commands(hass)
        state["commands_registered"] = True
    if not state.get("icons_registered"):
        from homeassistant.components.frontend import add_extra_js_url
        add_extra_js_url(hass,f"{_FILE_URL}/wachturm-icons.js?v={VERSION}")
        state["icons_registered"]=True
    if not state.get("card_registered"):
        from homeassistant.components.frontend import add_extra_js_url
        add_extra_js_url(hass,f"{_FILE_URL}/alarmzentrale-card.js?v={VERSION}")
        state["card_registered"]=True
    # A conflicting route must not be overwritten without confirmation.
    if PANEL_URL in hass.data.get("frontend_panels", {}):
        _LOGGER.warning("Alarmzentrale panel route already in use; refusing to override it")
        return
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="ha-alarmzentrale-panel",
        frontend_url_path=PANEL_URL,
        sidebar_title=NAME,
        sidebar_icon=DEFAULT_ICON,
        module_url=f"{_FILE_URL}/alarmzentrale-panel.js?v={VERSION}",
        require_admin=False,
        embed_iframe=False,
        trust_external=True,
    )
    hass.data[DOMAIN + "_editor"]["registered"] = True


async def async_unregister_editor(hass: HomeAssistant) -> None:
    """Remove our own sidebar panel; WebSocket handlers remain guarded."""
    if not hass.data.get(DOMAIN + "_editor", {}).get("registered"):
        return
    # Public removal is provided by Home Assistant's frontend component.
    from homeassistant.components import frontend
    frontend.async_remove_panel(hass, PANEL_URL)
    hass.data[DOMAIN + "_editor"]["registered"] = False
