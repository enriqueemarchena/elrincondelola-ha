from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import importlib
import sys

from .const import DOMAIN

PLATFORMS: list[str] = ["sensor", "binary_sensor"]

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up El Rincón de Lola from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    # Hot-reload de módulos de plataformas para recoger cambios sin reiniciar HA
    for mod_name in (
        "custom_components.elrincondelola.sensor",
        "custom_components.elrincondelola.binary_sensor",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None:
            try:
                importlib.reload(mod)
            except Exception:
                pass

    hass.data[DOMAIN][entry.entry_id] = {
        "host": entry.data.get("host"),
        "token": entry.data.get("token"),
    }
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
