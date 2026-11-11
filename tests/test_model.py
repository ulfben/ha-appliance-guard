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
    # Runs are ~28 min; defrost recovery runs ~45 min.
    assert 25 * 60 <= m.p99_run_s <= 50 * 60
    assert m.max_run_s <= 60 * 60
    # Pauses broken by HA restarts are not complete, so the 128-178 min "pauses"
    # (really missing data) don't count.
    assert m.max_pause_s <= 110 * 60
    # Between runs it normally draws ~0-5 W (22 W means something else is on).
    assert m.pause_level_p90 <= 10


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

def test_a_stall_is_not_learned_as_a_normal_run():
    # 25 Oct: the old dehumidifier "ran" for 16 h while failing.
    m = learn_cycling(raw("dehumidifier"), min_runs=10)
    assert m.max_run_s < 30 * 60


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
