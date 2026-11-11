"""Relearn button: learn again from the last 10 days, replacing what was kept."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GuardConfigEntry
from .entity import GuardEntity


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([RelearnButton(entry.runtime_data, "relearn")])


class RelearnButton(GuardEntity, ButtonEntity):
    async def async_press(self) -> None:
        self.watcher.learn_from = None
        await self.watcher.async_relearn(fresh=True)
