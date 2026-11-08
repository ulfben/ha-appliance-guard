"""Learning from the reference installation's raw history."""

import pytest
from replay import at, between, raw

from custom_components.appliance_guard.episodes import RUN, segment_all
from custom_components.appliance_guard.model import NotCyclingError, learn_cycling


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
