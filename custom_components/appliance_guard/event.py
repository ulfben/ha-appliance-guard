"""Finding event entity: fires on each notice, alarm and answer, for automations."""

from __future__ import annotations

from typing import Any

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GuardConfigEntry
from .entity import GuardEntity


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([FindingEvent(entry.runtime_data, "finding")])


class FindingEvent(GuardEntity, EventEntity):
    _attr_event_types = ["notice", "alarm", "response"]

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.watcher.async_add_event_listener(self._fire))

    @callback
    def _fire(self, kind: str, data: dict[str, Any]) -> None:
        self._trigger_event(kind, data)
        self.async_write_ha_state()
