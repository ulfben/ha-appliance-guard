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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import GuardConfigEntry
from .const import DOMAIN
from .detector import Profile
from .entity import GuardEntity
from .model import Model
from .watcher import Watcher

STATUSES = ["ok", "learning", "notice", "alarm", "off_on_purpose", "paused", "disabled"]


@dataclass(frozen=True, kw_only=True)
class LearnedDescription(SensorEntityDescription):
    value: Callable[[Watcher], float | None]
    applies: Callable[[Watcher], bool]  # only created where a check uses the value


LEARNED = [
    LearnedDescription(key="running_power", device_class=SensorDeviceClass.POWER,
                       native_unit_of_measurement=UnitOfPower.WATT,
                       value=lambda w: _m(w, lambda m: round(m.run_w, 1)),
                       applies=lambda w: w.cfg.profile == Profile.CYCLING),
    LearnedDescription(key="longest_pause", device_class=SensorDeviceClass.DURATION,
                       native_unit_of_measurement=UnitOfTime.MINUTES,
                       value=lambda w: _m(w, lambda m: round(m.max_pause_s / 60)),
                       applies=lambda w: w.cfg.profile == Profile.CYCLING and w.cfg.critical),
    LearnedDescription(key="normal_minimum_power", device_class=SensorDeviceClass.POWER,
                       native_unit_of_measurement=UnitOfPower.WATT,
                       value=lambda w: round(w.floor_w, 1) if w.floor_w is not None else None,
                       applies=lambda w: w.cfg.profile == Profile.ALWAYS_ON),
]
# Sensors earlier versions created, or that no longer apply to this plug.
OLD_KEYS = ["between_runs_power", "longest_run"]


def _m(w: Watcher, f: Callable[[Model], float]) -> float | None:
    return f(w.model) if w.model is not None else None


async def async_setup_entry(hass: HomeAssistant, entry: GuardConfigEntry,
                            async_add_entities: AddConfigEntryEntitiesCallback) -> None:
    w = entry.runtime_data
    learned = [d for d in LEARNED if d.applies(w)]
    entities: list[SensorEntity] = [StatusSensor(w), *(LearnedSensor(w, d) for d in learned)]
    if w.relay_entity:
        entities.append(AfterDropoutSensor(w))
    ent_reg = er.async_get(hass)
    gone = OLD_KEYS + [d.key for d in LEARNED if d not in learned]
    for key in gone + ([] if w.relay_entity else ["after_dropout"]):
        if eid := ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}"):
            ent_reg.async_remove(eid)
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
        return self.native_value is not None

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value(self.watcher)


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
