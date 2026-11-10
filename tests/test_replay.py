"""Reference cases R1-R12 (docs/DESIGN.md section 2) replayed through the detector."""

import random

import pytest
from replay import at, between, ha_stops, local, raw, relay, run

from custom_components.appliance_guard.detector import (
    DeviceConfig,
    DeviceDetector,
    Profile,
    Severity,
)
from custom_components.appliance_guard.model import NoSteadyDrawError, learn_cycling, learn_floor

FRIDGE = DeviceConfig("Basement fridge", Profile.CYCLING, rating_w=3680, critical=True)


def _checks(findings):
    return [(f.check, f.severity, local(f.ts)) for f in findings]


# ---- R3: fridge failed start after a mains blip ----------------------------

def test_r3_single_failed_start_that_recovers_is_one_notice():
    readings = raw("bsmnt_fridge")
    det = DeviceDetector(FRIDGE, learn_cycling(readings))
    found = [f for f in run(det, readings) if f.check == "A3"]
    assert len(found) == 1, _checks(found)
    f = found[0]
    assert f.severity == Severity.NOTICE
    # Failed at 15:02:11, normal start at 15:11:08.
    assert at("2026-11-02 15:11") <= f.ts < at("2026-11-02 15:12")


# ---- R12: normal compressor starts raise nothing ---------------------------

@pytest.mark.parametrize("alias", ["lg_fridge", "lg_freezer", "dehumidifier"])
def test_r12_normal_starts_raise_no_failed_start(alias):
    readings = raw(alias)
    det = DeviceDetector(DeviceConfig(alias, Profile.CYCLING),
                         learn_cycling(readings, min_runs=10))
    assert [f for f in run(det, readings) if f.check == "A3"] == []


# ---- R4: the kitchenette lights share the basement fridge's plug -------------
#
# At a 20 W threshold this looked like one 6.5 h run. At the learned threshold
# (~45 W) it is normal 28-min runs at ~111 W (89 W + 22 W) with 22 W in every
# pause: the two IKEA light drivers on the same extension cord. Each episode
# starts within a second of light.dimmable_light_7 turning on (confirmed by the
# user). Until shared loads are implemented, N4 raises a notice for each; with
# the light linked, these must go quiet (test to be added with shared loads).


def test_r4_shared_load_episodes_start_when_the_light_turns_on():
    light = [(ts, on) for ts, on, _ in relay("kitchenette_light") if on is not None]
    turned_on = [ts for (ts, on), (_, prev) in zip(light[1:], light, strict=False)
                 if on and not prev]
    readings = raw("bsmnt_fridge")
    for start in (at("2026-11-01 10:38"), at("2026-11-03 16:26")):
        assert any(abs(ts - start) < 60 for ts in turned_on), local(start)
        after = [w for ts, w in between(readings, start + 5, start + 600)
                 if w is not None and w < 45]
        # The lights fade in (9 W, then 22 W); the pause level settles at ~22 W.
        assert after and 18 <= after[-1] <= 26, after

def test_r4_elevated_draw_between_runs_is_a_notice_not_a_long_run():
    readings = raw("bsmnt_fridge")
    model = learn_cycling(between(readings, 0, at("2026-11-04 15:00")))
    det = DeviceDetector(FRIDGE, model)
    found = run(det, readings)
    assert [f for f in found if f.check == "N1"] == [], _checks(found)
    n4 = [f for f in found if f.check == "N4"]
    assert all(f.severity == Severity.NOTICE for f in n4)
    on_4_nov = [f for f in n4 if at("2026-11-04 17:36") <= f.ts <= at("2026-11-04 18:00")]
    assert len(on_4_nov) == 1, _checks(n4)
    # 1 Nov ~10:38 and 3 Nov ~16:26 show the same pattern (22 W between runs,
    # ~110 W runs); nothing else in the 10 days does.
    assert [local(f.ts)[:13] for f in n4] == [
        "2026-11-01 10", "2026-11-03 16", "2026-11-04 17"], _checks(n4)


def test_n1_a_run_that_never_stops_alarms():
    readings = between(raw("bsmnt_fridge"), 0, at("2026-11-04 00:00"))
    model = learn_cycling(readings)
    det = DeviceDetector(FRIDGE, model)
    run(det, readings)
    t0 = readings[-1][0] + 60
    stuck = [(t0 + 300 * i, 90.0 + (i % 3)) for i in range(60)]  # 5 h at running level
    found = [f for f in run(det, stuck) if f.check == "N1"]
    assert [f.severity for f in found] == [Severity.NOTICE, Severity.ALARM], _checks(found)
    assert found[0].ts - t0 <= 2 * 3600
    assert found[1].ts - t0 <= 3 * model.max_run_s + 600


# ---- R10 / N2: missing data is never a dead fridge ---------------------------

@pytest.mark.parametrize("alias", ["bsmnt_fridge", "lg_fridge", "lg_freezer"])
def test_r10_no_dead_device_from_unavailable_gaps(alias):
    readings = raw(alias)
    half = readings[len(readings) // 2][0]
    model = learn_cycling(between(readings, 0, half))
    det = DeviceDetector(DeviceConfig(alias, Profile.CYCLING, critical=True), model)
    found = [f for f in run(det, between(readings, 0, 2e10)) if f.check == "N2"]
    assert found == [], _checks(found)


def test_n2_freezer_that_stops_starting_alarms_within_40_min():
    readings = raw("lg_freezer")
    model = learn_cycling(readings)
    cut = at("2026-11-06 12:00")
    dead = [(ts, w if ts < cut else 1.5) for ts, w in between(readings, 0, at("2026-11-06 16:00"))]
    det = DeviceDetector(DeviceConfig("Freezer", Profile.CYCLING, critical=True), model)
    found = [f for f in run(det, dead) if f.check == "N2"]
    assert len(found) == 1 and found[0].severity == Severity.ALARM
    assert found[0].ts - cut <= 40 * 60 + model.max_run_s


# ---- N2 for demand-driven appliances: a dry night is not a dead dehumidifier ---

def _dehumidifier_night():
    """Model learned from the reference days, and the real night of 2026-11-09.

    The compressor started every ~64 min until 21:03, then the air stayed dry (humidity
    samples 51-56 %, target 55 %) and it only ran its fan for ~1 min every ~63 min
    (13-14 W) to sample the air. On 2026-11-09 23:12 this raised a false N2 notice.
    """
    model = learn_cycling(raw("dehumidifier"))
    return model, raw("dehumidifier_dry_night")


def test_n2_dry_night_with_fan_samples_is_quiet():
    model, night = _dehumidifier_night()
    det = DeviceDetector(DeviceConfig("Dehumidifier", Profile.CYCLING), model)
    found = [f for f in run(det, night) if f.check == "N2"]
    assert found == [], _checks(found)


def test_n2_dehumidifier_flat_at_idle_still_gets_a_notice():
    model, night = _dehumidifier_night()
    cut = at("2026-11-09 21:10")
    dead = [(ts, w if ts < cut or w is None else min(w, 1.0)) for ts, w in night]
    det = DeviceDetector(DeviceConfig("Dehumidifier", Profile.CYCLING), model)
    found = [f for f in run(det, dead) if f.check == "N2"]
    assert len(found) == 1 and found[0].severity == Severity.NOTICE, _checks(found)
    assert "no sign of life" in found[0].summary
    limit = max(1.5 * model.max_pause_s, 1800)
    assert found[0].ts - cut <= limit + 600


def test_n2_critical_appliance_ignores_signs_of_life():
    model, night = _dehumidifier_night()
    det = DeviceDetector(DeviceConfig("Dehumidifier", Profile.CYCLING, critical=True), model)
    found = [f for f in run(det, night) if f.check == "N2"]
    assert len(found) == 1 and found[0].severity == Severity.ALARM, _checks(found)


# ---- R1: dehumidifier stall (synthetic, shaped like the hourly statistics) ---

def _old_dehumidifier(days=2, seed=1):
    """Normal cycles: 0.6 W idle ~60 min, a 450 W surge, ~270 W for ~40 min."""
    rnd, t, out = random.Random(seed), 0.0, []
    while t < days * 86400:
        out.append((t, 0.6))
        t += rnd.uniform(45, 75) * 60
        out.append((t, 450.0))
        t += 2
        end = t + rnd.uniform(30, 50) * 60
        while t < end:
            out.append((t, 270 + rnd.uniform(-5, 5)))
            t += 200
    return out, t


@pytest.mark.parametrize("readings_per_attempt", [1, 3])
def test_r1_repeated_failed_starts_alarm_within_15_min(readings_per_attempt):
    normal, t0 = _old_dehumidifier()
    det = DeviceDetector(DeviceConfig("Dehumidifier", Profile.CYCLING, rating_w=2300),
                         learn_cycling(normal))
    stall, t = [(t0, 0.6)], t0 + 600
    fail_start = t
    for _ in range(20):  # a 1150 W attempt every 3 min, cut after ~15 s
        for i in range(readings_per_attempt):
            stall.append((t + 5 * i, 1150.0 - 5 * i))
        stall.append((t + 15 + 5 * readings_per_attempt, 0.6))
        t += 180
    assert [f for f in run(det, normal) if f.check == "A3"] == []
    found = [f for f in run(det, stall) if f.check == "A3"]
    assert [f.severity for f in found] == [Severity.ALARM]
    assert found[0].ts - fail_start <= 15 * 60
    assert "cut_power" in found[0].data["offer"]


# ---- R5: over the plug's rating ---------------------------------------------

def test_r5_over_rating_alarms_once_and_rearms():
    det = DeviceDetector(DeviceConfig("Kitchen island", Profile.ON_DEMAND, rating_w=2500))
    kettle = [(0, 0.3), (60, 2000.0), (300, 0.3)]
    assert run(det, kettle) == []
    both = [(1000, 2000.0), (1010, 3302.0), (1020, 3290.0), (1200, 0.3)]
    found = run(det, both)
    assert [(f.check, f.severity) for f in found] == [("A1", Severity.ALARM)]
    again = [(2000, 3302.0)]  # rearmed after 5 min below 80 %
    assert [f.check for f in run(det, again)] == ["A1"]


def test_a1_sustained_near_rating():
    det = DeviceDetector(DeviceConfig("Plug", Profile.ON_DEMAND, rating_w=2500))
    found = run(det, [(0, 2300.0), (90, 2310.0)], tick_s=10)
    assert [f.check for f in found] == ["A1"] and found[0].ts == 60


def test_impossible_readings_are_dropped():
    det = DeviceDetector(DeviceConfig("Plug", Profile.ON_DEMAND, rating_w=2300))
    assert det.feed(0, 34359738368.0) == [] and det.dropped == 1


# ---- R2 / A2: relay switched off by itself -----------------------------------

def test_a2_relay_off_without_ha_command_alarms():
    det = DeviceDetector(DeviceConfig("Dehumidifier", Profile.CYCLING))
    assert det.feed_relay(0, True, commanded=False) == []
    assert det.feed_relay(10, False, commanded=True) == []  # an automation / Fuse Guard
    assert det.feed_relay(20, True, commanded=True) == []
    found = det.feed_relay(30, False, commanded=False)  # overload protection tripped
    assert [(f.check, f.severity) for f in found] == [("A2", Severity.ALARM)]


def test_a2_reference_relays_had_no_self_switch_offs():
    """22-66 dropouts and HA restarts per plug in the window; every one came back on."""
    stops = ha_stops()
    for alias in ("bsmnt_fridge_switch", "lg_fridge_switch", "lg_freezer_switch",
                  "dehumidifier_switch", "kitchen_island_switch", "servers_printer_switch"):
        det = DeviceDetector(DeviceConfig(alias, Profile.CYCLING))
        events = sorted([(ts, on, cmd) for ts, on, cmd in relay(alias)]
                        + [(t, "stop", False) for t in stops], key=lambda e: e[0])
        found = []
        for ts, on, cmd in events:
            if on == "stop":
                det.feed_ha_down(ts)
            else:
                found += det.feed_relay(ts, on, cmd) + det.tick(ts + 400)
        assert found == [], alias
        assert all(d.outcome == "on" and not d.ha_down for d in det.dropouts), alias


# ---- A5: came back off after a dropout -----------------------------------------

def _fridge() -> DeviceDetector:
    det = DeviceDetector(DeviceConfig("Fridge", Profile.CYCLING, critical=True))
    det.feed_relay(0, True, commanded=False)
    return det


def test_a5_plug_that_comes_back_off_alarms_after_the_grace_time():
    det = _fridge()
    det.feed_relay(100, None, commanded=False)  # power cut: the plug drops off
    assert det.feed_relay(2500, False, commanded=False) == []  # back, but off
    assert det.tick(2650) == []  # some plugs blink off while booting
    found = det.tick(2810)
    assert [(f.check, f.severity) for f in found] == [("A5", Severity.ALARM)]
    assert "40 min out of contact" in found[0].summary
    assert [(d.outcome, d.gap_s) for d in det.dropouts] == [("off", 2400)]
    assert det.tick(3000) == []  # once


def test_a5_plug_that_restores_power_is_recorded_and_silent():
    det = _fridge()
    det.feed_relay(100, None, commanded=False)
    assert det.feed_relay(400, True, commanded=False) == []
    assert det.tick(800) == []
    assert [(d.outcome, d.ha_down) for d in det.dropouts] == [("on", False)]


def test_a5_boot_blink_off_then_on_counts_as_restoring():
    det = _fridge()
    det.feed_relay(100, None, commanded=False)
    det.feed_relay(400, False, commanded=False)
    det.feed_relay(405, True, commanded=False)
    assert det.tick(800) == []
    assert [d.outcome for d in det.dropouts] == ["on"]


def test_a5_turned_on_from_ha_in_time_is_recorded_as_back_off():
    det = _fridge()
    det.feed_relay(100, None, commanded=False)
    det.feed_relay(400, False, commanded=False)
    det.feed_relay(420, True, commanded=True)  # an automation fixed it
    assert det.tick(800) == []
    assert [d.outcome for d in det.dropouts] == ["off"]


def test_a5_flapping_back_into_unavailable_keeps_waiting():
    det = _fridge()
    det.feed_relay(100, None, commanded=False)
    det.feed_relay(400, False, commanded=False)
    det.feed_relay(410, None, commanded=False)
    assert det.tick(800) == []  # unknown again: no verdict yet
    det.feed_relay(900, False, commanded=False)
    assert [f.check for f in det.tick(1300)] == ["A5"]


def test_a5_no_alarm_when_it_was_off_before_or_switched_off_from_ha():
    det = DeviceDetector(DeviceConfig("Fridge", Profile.CYCLING))
    det.feed_relay(0, False, commanded=True)
    det.feed_relay(100, None, commanded=False)
    det.feed_relay(400, False, commanded=False)
    assert det.tick(800) == [] and det.dropouts == []
    det = _fridge()
    det.feed_relay(100, None, commanded=False)
    det.feed_relay(400, False, commanded=True)
    assert det.tick(800) == []


def test_a5_across_an_ha_restart():
    det = _fridge()
    det.feed_ha_down(1000)
    det.feed_relay(1300, None, commanded=False)  # unavailable at start (Zigbee)
    det.feed_relay(5000, False, commanded=False)
    found = det.tick(5400)
    assert [f.check for f in found] == ["A5"] and "Home Assistant was down" in found[0].summary
    assert [(d.outcome, d.ha_down) for d in det.dropouts] == [("off", True)]


def test_a5_relay_still_on_after_an_ha_restart_records_nothing():
    det = _fridge()
    det.feed_ha_down(1000)
    det.feed_relay(1300, True, commanded=False)
    assert det.tick(1500) == [] and det.dropouts == []


# ---- R9: printer warm-ups on an always-on plug --------------------------------

def test_r9_printer_warmups_raise_nothing():
    det = DeviceDetector(DeviceConfig("Servers", Profile.ALWAYS_ON, rating_w=2300))
    assert run(det, raw("servers_printer")) == []


# ---- always-on: S2 stopped drawing, N5 switched off from HA -------------------

NETWORK = DeviceConfig("Network", Profile.ALWAYS_ON, rating_w=3680, critical=True)


def test_always_on_floor_is_learned_from_real_history():
    assert 65 <= learn_floor(raw("network")) <= 72
    # The printer's warm-ups and a reboot's 0 W blip don't move the servers' floor.
    assert 40 <= learn_floor(raw("servers_printer")) <= 55


def test_always_on_floor_needs_a_steady_draw():
    with pytest.raises(NoSteadyDrawError):
        learn_floor([(t * 60.0, 0.0) for t in range(3000)])
    with pytest.raises(NoSteadyDrawError):
        learn_floor([(t * 60.0, 70.0) for t in range(100)])  # under a day


@pytest.mark.parametrize("alias", ["network", "servers_printer"])
def test_s2_real_always_on_history_raises_nothing(alias):
    readings = raw(alias)
    cfg = DeviceConfig(alias, Profile.ALWAYS_ON, rating_w=2300, critical=True)
    det = DeviceDetector(cfg, floor_w=learn_floor(readings))
    assert [f for f in run(det, readings) if f.check == "S2"] == []


def _network(**kw):
    return DeviceDetector(kw.pop("cfg", NETWORK), floor_w=70.0)


def test_s2_load_that_stops_drawing_alarms_once_after_5_min():
    det = _network()
    det.feed_relay(0, True, commanded=True)
    det.feed(10, 72.0)
    det.feed(100, 0.0)  # the power supply died; the relay is still on
    assert det.tick(390) == []
    found = det.tick(410)
    assert [(f.check, f.severity) for f in found] == [("S2", Severity.ALARM)]
    assert "although its plug is on" in found[0].summary
    assert det.tick(2000) == []
    det.feed(2100, 71.0)  # back: the next stop is news again
    det.feed(2200, 0.0)
    assert [f.check for f in det.tick(2600)] == ["S2"]


def test_s2_non_critical_is_a_notice():
    det = _network(cfg=DeviceConfig("Servers", Profile.ALWAYS_ON))
    det.feed(0, 0.0)
    assert [(f.check, f.severity) for f in det.tick(400)] == [("S2", Severity.NOTICE)]


def test_s2_relay_off_or_plug_out_of_contact_is_not_a_stop():
    det = _network()
    det.feed_relay(0, True, commanded=True)
    det.feed(10, 72.0)
    det.feed_relay(100, False, commanded=True)
    det.feed(101, 0.0)
    assert det.tick(1000) == []
    det.feed_relay(1100, True, commanded=True)
    det.feed(1101, None)  # the plug dropped out
    assert det.tick(2000) == []


def test_s2_turned_back_on_but_still_dead_counts_from_the_relay():
    det = _network()
    det.feed_relay(0, True, commanded=True)
    det.feed(10, 0.0)
    det.feed_relay(20, False, commanded=False)
    det.feed_relay(1000, True, commanded=True)  # no new power reading follows
    assert [f.check for f in det.tick(1200)] == []
    assert [f.check for f in det.tick(1310)] == ["S2"]


def test_s2_only_for_always_on():
    det = DeviceDetector(DeviceConfig("Kettle", Profile.ON_DEMAND), floor_w=70.0)
    det.feed(0, 0.0)
    assert det.tick(1000) == []


def test_n5_critical_always_on_switched_off_from_ha_is_a_notice():
    det = _network()
    det.feed_relay(0, True, commanded=True)
    found = det.feed_relay(100, False, commanded=True)
    assert [(f.check, f.severity) for f in found] == [("N5", Severity.NOTICE)]
    det.feed_relay(200, True, commanded=True)
    assert [f.check for f in det.feed_relay(300, False, commanded=False)] == ["A2"]


@pytest.mark.parametrize("cfg", [DeviceConfig("Servers", Profile.ALWAYS_ON),
                                 DeviceConfig("Fridge", Profile.CYCLING, critical=True)])
def test_n5_not_for_other_plugs(cfg):
    det = DeviceDetector(cfg)
    det.feed_relay(0, True, commanded=True)
    assert det.feed_relay(100, False, commanded=True) == []


def test_s2_a_relay_on_for_days_counts_from_the_last_reading():
    det = _network()
    det.feed_relay(0, True, commanded=True)
    det.feed(5000, 0.0)
    det.feed_relay(9000, True, commanded=True)  # e.g. seeded again after a restart
    found = det.tick(9000)
    assert [f.check for f in found] == ["S2"]
    assert "stopped drawing power 67 min ago" in found[0].summary
