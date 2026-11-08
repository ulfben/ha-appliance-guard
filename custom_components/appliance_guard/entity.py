"""Base entity: one service device per watched plug."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .watcher import Watcher


class GuardEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, watcher: Watcher, key: str) -> None:
        self.watcher = watcher
        self._attr_translation_key = key
        self._attr_unique_id = f"{watcher.entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, watcher.entry.entry_id)},
            name=f"{watcher.entry.title} guard",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.watcher.async_add_listener(self.async_write_ha_state))
