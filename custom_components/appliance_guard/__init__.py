"""Appliance Guard: notices when a plug-monitored appliance behaves like it is broken.

One config entry watches one plug. The detection logic (episodes, model, detector) is
plain Python; watcher.py connects it to Home Assistant. See docs/DESIGN.md.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .watcher import STORE_VERSION, Watcher

type GuardConfigEntry = ConfigEntry[Watcher]

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.EVENT,
    Platform.SELECT,
    Platform.SENSOR,
]

__all__ = ["DOMAIN"]


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry) -> bool:
    watcher = Watcher(hass, entry)
    entry.runtime_data = watcher
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await watcher.async_start()
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GuardConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GuardConfigEntry) -> None:
    """Forget what was recorded about this plug."""
    await Store(hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def _async_reload(hass: HomeAssistant, entry: GuardConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
