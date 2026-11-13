"""Learning from the reference installation's raw history."""

import pytest
from replay import at, between, raw

from custom_components.appliance_guard.episodes import RUN, segment_all
from custom_components.appliance_guard.model import NotCyclingError, learn_cycling, without


def test_basement_fridge_levels_and_rhythm():
    m = learn_cycling(between(raw("bsmnt_fridge"), 0, at("2026-11-04 15:00")))
    assert m.idle_w < 10
    assert 85 <= m.run_w <= 95
    assert m.idle_w < m.threshold_w < m.run_w
    assert m.runs >= 60
    # Pauses broken by HA restarts are not complete, so the 128-178 min "pauses"
    # (really missing data) don't count.
    assert m.max_pause_s <= 110 * 60


def test_freezer_pauses_are_short_and_regular():
    m = learn_cycling(raw("lg_freezer"))
    assert 30 <= m.run_w <= 50
    assert m.max_pause_s <= 25 * 60


def test_lg_fridge_idle_reads_zero_but_is_alive():
    m = learn_cycling(raw("lg_fridge"))
    assert m.idle_w < 3
    assert 38 <= m.run_w <= 52


def test_dehumidifier_running_level():
    m = learn_cycling(raw("dehumidifier"), min_runs=10)
    assert 250 <= m.run_w <= 290


def test_steady_load_is_not_cycling():
    with pytest.raises(NotCyclingError):
        learn_cycling(raw("network"))


def test_unknown_gap_with_same_state_continues_the_segment():
    # idle, unknown for an hour, still idle, then a run: one idle segment whose
    # known length excludes the gap.
    readings = [(0, 1.0), (600, None), (4200, 1.0), (4800, 90.0), (6000, 1.0)]
    segs = segment_all(readings, 40)
    idle, run_ = segs
    assert idle.known_s == 600 + 600
    assert idle.end == 4800 and not idle.complete  # began at the first reading ever
    assert run_.state == RUN and run_.complete and run_.known_s == 1200


# ---- what is learned must not take in faults --------------------------------------

def test_a_long_but_real_tail_is_kept():
    # The LG fridge really does pause for up to ~2.75 h now and then.
    assert learn_cycling(raw("lg_fridge")).max_pause_s >= 160 * 60


def _freezer(days: int = 3) -> list[tuple[float, float | None]]:
    out: list[tuple[float, float | None]] = []
    for i in range(days * 24):
        out += [(i * 3600.0, 40.0), (i * 3600.0 + 1200, 1.0)]
    return out


def test_time_switched_off_is_not_a_pause():
    readings = _freezer()
    # Switched off for an hour after a run: short enough to pass as a long pause.
    off = (30 * 3600.0 + 1300, 31 * 3600.0 + 1300)
    readings = [r for r in readings if not off[0] <= r[0] < off[1]] + [(off[0], 0.0)]
    readings.sort()
    assert learn_cycling(readings).max_pause_s > 90 * 60
    assert learn_cycling(without(readings, [off])).max_pause_s <= 41 * 60


def test_without_turns_the_gaps_into_unknown_time():
    readings = [(0.0, 1.0), (10.0, 2.0), (20.0, 3.0), (30.0, 4.0)]
    assert without(readings, [(5.0, 25.0)]) == [(0.0, 1.0), (5.0, None), (30.0, 4.0)]
    assert without(readings, [(40.0, 50.0)])[-1] == (40.0, None)


def test_a_load_that_mostly_runs_over_a_wide_range_still_has_an_idle_level():
    """The LG freezer: idle 1.4 W, running 85 % of the time at 33-90 W.

    Split in watts, the running range was split in two (31 / 86 W) and the
    freezer was never learned. On a log scale idle and running are far apart.
    """
    readings: list[tuple[float, float | None]] = []
    t = 0.0
    for _ in range(100):
        for i in range(120):
            readings.append((t + i * 60, 85.0 + i % 3 if i < 30 else 33.0 + i % 10))
        t += 120 * 60
        readings.append((t, 1.4))
        t += 20 * 60
    m = learn_cycling(readings)
    assert m.idle_w < 2 and 30 < m.run_w < 90 and m.threshold_w < 33
    assert 19 * 60 <= m.max_pause_s <= 21 * 60


def test_setup_only_calls_it_cycling_when_it_can_be_learned():
    """15 runs in 2.5 days cycles like a compressor, but too few runs to learn yet."""
    from custom_components.appliance_guard.model import summarize

    readings: list[tuple[float, float | None]] = []
    for i in range(15):
        t = i * 4 * 3600
        readings += [(t, 90.0), (t + 1200, 1.0)]
    readings.append((15 * 4 * 3600, 1.0))
    assert summarize(readings).profile == "on_demand"
    readings += [(15 * 4 * 3600 + i * 4 * 3600 + d, w) for i in range(6)
                 for d, w in ((600, 90.0), (1800, 1.0))]
    assert summarize(readings).profile == "cycling"
