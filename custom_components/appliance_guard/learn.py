"""Read a power sensor's history from the recorder, in the form the core expects."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder import get_instance, history
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, State
from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

Reading = tuple[float, float | None]


def to_watts(state: State | None) -> float | None:
    if state is None:
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None  # unavailable, unknown, or not a number
    unit = state.attributes.get("unit_of_measurement")
    return value * 1000 if unit == "kW" else value


async def async_load_readings(hass: HomeAssistant, entity_id: str, days: float) -> list[Reading]:
    """Readings for the last `days`, with an unknown reading at every HA shutdown.

    The recorder writes nothing while Home Assistant is down, so without the markers
    a pause spanning a restart would look like one long known pause.
    """
    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    rec = get_instance(hass)
    states = await rec.async_add_executor_job(_states, hass, entity_id, start, end)
    stops = await rec.async_add_executor_job(_stops, hass, start)
    readings: list[Reading] = [(s.last_updated_timestamp, to_watts(s)) for s in states]
    readings += [(t, None) for t in stops]
    readings.sort(key=lambda r: r[0])
    return readings


async def async_load_relay(hass: HomeAssistant, entity_id: str,
                           days: float) -> tuple[list[tuple[float, str]], list[float]]:
    """A relay's raw states for the last `days`, and the HA shutdown times."""
    end = dt_util.utcnow()
    start = end - timedelta(days=days)
    rec = get_instance(hass)
    states = await rec.async_add_executor_job(_states, hass, entity_id, start, end)
    stops = await rec.async_add_executor_job(_stops, hass, start)
    return [(s.last_updated_timestamp, s.state) for s in states], stops


def _states(hass: HomeAssistant, entity_id: str, start: datetime, end: datetime) -> list[State]:
    found = history.state_changes_during_period(
        hass, start, end, entity_id, no_attributes=False, include_start_time_state=True)
    return found.get(entity_id, [])


def _stops(hass: HomeAssistant, start: datetime) -> list[float]:
    """Times of homeassistant_stop events. Recorder internals, so best effort."""
    try:
        from homeassistant.components.recorder.db_schema import Events, EventTypes
        from homeassistant.components.recorder.util import session_scope

        with session_scope(hass=hass, read_only=True) as session:
            rows = (
                session.query(Events.time_fired_ts)
                .join(EventTypes, Events.event_type_id == EventTypes.event_type_id)
                .filter(EventTypes.event_type == EVENT_HOMEASSISTANT_STOP)
                .filter(Events.time_fired_ts >= start.timestamp())
                .all()
            )
        return [r[0] for r in rows]
    except Exception:  # noqa: BLE001 - learning still works, only less precisely
        _LOGGER.debug("Could not read HA stop events from the recorder", exc_info=True)
        return []
