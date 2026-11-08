"""Mode select: watching / off on purpose / paused / disabled. Restored across restarts."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import GuardConfigEntry
from .const import MODES
from .entity import GuardEntity


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ModeSelect(entry.runtime_data, "mode")])


class ModeSelect(GuardEntity, SelectEntity, RestoreEntity):
    _attr_options = MODES

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in MODES:
            self.watcher.set_mode(last.state)

    @property
    def current_option(self) -> str:
        return self.watcher.mode

    async def async_select_option(self, option: str) -> None:
        self.watcher.set_mode(option)
