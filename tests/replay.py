"""Load reference fixtures and replay them through a detector."""

from __future__ import annotations

import csv
import gzip
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from custom_components.appliance_guard.detector import DeviceDetector, Finding

REF = Path(__file__).parent / "fixtures" / "reference"
TZ = ZoneInfo("Europe/Stockholm")

Reading = tuple[float, float | None]


def at(local: str) -> float:
    """'2026-11-02 15:02' (reference install's local time) -> epoch seconds."""
    return datetime.strptime(local, "%Y-%m-%d %H:%M").replace(tzinfo=TZ).timestamp()


def local(ts: float) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%Y-%m-%d %H:%M:%S")


def ha_stops() -> list[float]:
    with open(REF / "household" / "ha_lifecycle.csv") as f:
        return [float(r["ts"]) for r in csv.DictReader(f) if r["event"] == "homeassistant_stop"]


def raw(alias: str) -> list[Reading]:
    """Readings with `None` for unavailable/unknown.

    The recorder writes nothing while HA is down, so every homeassistant_stop is
    added as an unknown reading: the live runtime can't see that time either.
    """
    out: list[Reading] = []
    with gzip.open(REF / "raw" / f"{alias}.csv.gz", "rt") as f:
        for row in csv.DictReader(f):
            try:
                w: float | None = float(row["state"])
            except ValueError:
                w = None  # unavailable / unknown
            out.append((float(row["ts"]), w))
    if out:
        out += [(t, None) for t in ha_stops() if out[0][0] < t < out[-1][0]]
        out.sort(key=lambda r: r[0])
    return out


def relay(alias: str) -> list[tuple[float, bool | None, bool]]:
    """(ts, on, commanded) from a switch fixture."""
    out = []
    with gzip.open(REF / "raw" / f"{alias}.csv.gz", "rt") as f:
        for row in csv.DictReader(f):
            on = {"on": True, "off": False}.get(row["state"])
            out.append((float(row["ts"]), on, row["ctx_user"] == "1" or row["ctx_parent"] == "1"))
    return out


def between(readings: list[Reading], start: float, end: float) -> list[Reading]:
    return [r for r in readings if start <= r[0] < end]


def run(det: DeviceDetector, readings: list[Reading], tick_s: float = 60.0) -> list[Finding]:
    """Feed every reading, ticking the clock every `tick_s` in between."""
    out: list[Finding] = []
    prev = None
    for ts, w in readings:
        if prev is not None:
            t = prev + tick_s
            while t < ts:
                out += det.tick(t)
                t += tick_s
        out += det.feed(ts, w)
        prev = ts
    return out
