"""Status and what-was-learned sensors."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import GuardConfigEntry
from .entity import GuardEntity
from .model import Model
from .watcher import Watcher

STATUSES = ["ok", "learning", "notice", "alarm", "off_on_purpose", "paused", "disabled"]


@dataclass(frozen=True, kw_only=True)
class LearnedDescription(SensorEntityDescription):
    value: Callable[[Model], float]


LEARNED = [
    LearnedDescription(key="running_power", device_class=SensorDeviceClass.POWER,
                       native_unit_of_measurement=UnitOfPower.WATT,
                       value=lambda m: round(m.run_w, 1)),
    LearnedDescription(key="between_runs_power", device_class=SensorDeviceClass.POWER,
                       native_unit_of_measurement=UnitOfPower.WATT,
                       value=lambda m: round(m.pause_level_p90, 1)),
    LearnedDescription(key="longest_run", device_class=SensorDeviceClass.DURATION,
                       native_unit_of_measurement=UnitOfTime.MINUTES,
                       value=lambda m: round(m.max_run_s / 60)),
    LearnedDescription(key="longest_pause", device_class=SensorDeviceClass.DURATION,
                       native_unit_of_measurement=UnitOfTime.MINUTES,
                       value=lambda m: round(m.max_pause_s / 60)),
]


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    w = entry.runtime_data
    entities: list[SensorEntity] = [StatusSensor(w), *(LearnedSensor(w, d) for d in LEARNED)]
    if w.relay_entity:
        entities.append(AfterDropoutSensor(w))
    async_add_entities(entities)


class StatusSensor(GuardEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    def __init__(self, watcher: Watcher) -> None:
        super().__init__(watcher, "status")

    @property
    def native_value(self) -> str:
        return self.watcher.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        w = self.watcher
        f = w.last_finding
        return {
            "open_findings": [x.summary for x in w.findings.values()],
            "last_finding": f.summary if f else None,
            "mode": w.mode,
            "learning_note": w.learn_note,
            "watched_entity": w.power_entity,
        }


class LearnedSensor(GuardEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    entity_description: LearnedDescription

    def __init__(self, watcher: Watcher, description: LearnedDescription) -> None:
        super().__init__(watcher, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return self.watcher.model is not None

    @property
    def native_value(self) -> float | None:
        m = self.watcher.model
        return self.entity_description.value(m) if m else None


class AfterDropoutSensor(GuardEntity, SensorEntity):
    """What the relay did the last times the plug came back after losing contact.

    "Came back on" can also follow a radio dropout without any power cut, so only
    "came back off" proves the plug doesn't restore power.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["not_seen", "came_back_on", "came_back_off"]

    def __init__(self, watcher: Watcher) -> None:
        super().__init__(watcher, "after_dropout")

    @property
    def native_value(self) -> str:
        return self.watcher.after_dropout

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        rec = self.watcher.dropout_record
        last = rec.get("last") or {}
        ts = last.get("ts")
        return {
            "times_back_on": rec["back_on"],
            "times_back_off": rec["back_off"],
            "last_seen": dt_util.utc_from_timestamp(ts).isoformat() if ts else None,
            "last_outcome": last.get("outcome"),
            "last_gap_min": round(last["gap_s"] / 60) if ts else None,
            "last_while_ha_down": last.get("ha_down"),
        }
