"""Problem: on while an alarm is open."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import GuardConfigEntry
from .detector import Severity
from .entity import GuardEntity


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    async_add_entities([ProblemSensor(entry.runtime_data, "problem")])


class ProblemSensor(GuardEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool:
        return any(f.severity == Severity.ALARM for f in self.watcher.findings.values())
