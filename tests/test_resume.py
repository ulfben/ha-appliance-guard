"""What was already raised survives a silent replay of history (restart, reload, relearn)."""

from __future__ import annotations

from custom_components.appliance_guard.detector import (
    DeviceConfig,
    DeviceDetector,
    Profile,
    Reported,
)
from custom_components.appliance_guard.model import learn_cycling

CFG = DeviceConfig("Freezer", Profile.CYCLING, critical=True)
H = 3600.0


def _history(end: float, cycles: int = 40) -> list[tuple[float, float | None]]:
    """A healthy freezer (20 min at 40 W, 40 min at 1 W) ending in a pause at `end`."""
    out: list[tuple[float, float | None]] = []
    t = end - cycles * H
    for _ in range(cycles):
        out += [(t, 40.0), (t + 1200, 1.0)]
        t += H
    return out


def _dead_freezer():
    """It stopped starting: the last run ended 5 h before `now`, idle readings since."""
    now = 1_000_000.0
    readings = _history(now - 5 * H)
    readings += [(now - 4 * H, 2.0), (now - 3 * H, 1.0)]  # idle readings keep coming
    return now, readings, learn_cycling(readings)


def _replay(model, readings, since: float, now: float) -> tuple[DeviceDetector, list]:
    det = DeviceDetector(CFG, model)
    found = []
    for ts, w in readings:
        if ts >= since:
            found += det.feed(ts, w)
    return det, found


def test_a_problem_nobody_was_told_about_is_raised_after_the_replay():
    now, readings, model = _dead_freezer()
    det, found = _replay(model, readings, now - 12 * H, now)
    assert [f.check for f in found] == ["N2"]  # raised during the replay, so never shown

    det.resume([])
    assert [(f.check, f.severity) for f in det.tick(now)] == [("N2", "alarm")]


def test_one_already_raised_is_not_raised_again():
    now, readings, model = _dead_freezer()
    live, _ = _replay(model, readings, 0, now)  # watched live all along
    assert [f.check for f in live.tick(now)] == []  # raised when it happened
    reported = live.reported(now)
    assert [(r.check, r.severity) for r in reported] == [("N2", "alarm")]

    det, _ = _replay(model, readings, now - 12 * H, now)
    det.resume(reported)
    assert det.tick(now) == []


def test_a_replay_that_starts_partway_through_the_pause_is_the_same_episode():
    now, readings, model = _dead_freezer()
    live, _ = _replay(model, readings, 0, now - 3.5 * H)
    live.tick(now - 3.5 * H)
    reported = live.reported(now - 3.5 * H)
    assert reported and reported[0].start < now - 5 * H  # where the last run ended

    # The replay window begins after the pause started: its first reading opens it.
    det, _ = _replay(model, readings, now - 4.5 * H, now)
    assert det.seg.current.start == now - 4 * H
    det.resume(reported)
    assert det.tick(now) == []


def test_a_later_episode_is_news():
    now, readings, model = _dead_freezer()
    # Raised for an earlier pause that ended (it started again afterwards).
    old = Reported("N2", "alarm", now - 30 * H, now - 28 * H)
    det, _ = _replay(model, readings, now - 12 * H, now)
    det.resume([old])
    assert [f.check for f in det.tick(now)] == ["N2"]
