"""Per-device checks: a reading stream goes in, findings come out.

Implemented (DESIGN.md section 6): A1 plug rating, A2 relay off by itself,
A3 failed starts, A5 back off after a dropout, N1 long run, N2 dead device,
N4 elevated draw between runs.
A4, N3, S1, S2 and the household checks come later. Pure Python, no Home Assistant
imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .episodes import IDLE, RUN, Segment, Segmenter
from .model import Model


class Profile(StrEnum):
    CYCLING = "cycling"
    ALWAYS_ON = "always_on"
    ON_DEMAND = "on_demand"


class Severity(StrEnum):
    NOTICE = "notice"
    ALARM = "alarm"


@dataclass(frozen=True)
class Finding:
    check: str
    severity: Severity
    ts: float
    summary: str
    data: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Dropout:
    """What the relay did when the plug came back after losing contact."""

    ts: float
    outcome: str  # "on" or "off"
    gap_s: float
    ha_down: bool  # Home Assistant was down during the gap


@dataclass(frozen=True)
class DeviceConfig:
    name: str
    profile: Profile
    rating_w: float | None = None
    critical: bool = False


@dataclass(frozen=True)
class Limits:
    sanity_factor: float = 3.0  # readings above rating x this are dropped
    sanity_abs_w: float = 20000.0
    a1_sustain_frac: float = 0.9
    a1_sustain_s: float = 60.0
    a1_rearm_frac: float = 0.8
    a1_rearm_s: float = 300.0
    a3_factor: float = 2.5  # x running level
    a3_max_s: float = 120.0
    a3_window_s: float = 1800.0
    a3_alarm_count: int = 3
    a3_recover_s: float = 900.0
    n1_factor: float = 1.5
    n1_margin_s: float = 2700.0
    n1_alarm_factor: float = 3.0
    n1_elevated: float = 1.2
    n1_elevated_after_s: float = 7200.0
    n2_factor: float = 1.5
    n2_floor_s: float = 1800.0
    # Above idle by this much counts as a sign of life between runs: a dehumidifier's fan
    # sampling the air, a display. Non-critical appliances only get N2 when none is seen.
    n2_life_w: float = 5.0
    n4_margin_w: float = 10.0  # above the learned between-runs level
    n4_hold_s: float = 900.0
    n4_cooldown_s: float = 6 * 3600.0
    # Plugs and HA take a while to settle after a power cut, and "off" is rarely urgent.
    a5_grace_s: float = 300.0


class DeviceDetector:
    def __init__(self, cfg: DeviceConfig, model: Model | None = None,
                 limits: Limits | None = None) -> None:
        self.cfg = cfg
        self.model = model
        self.lim = limits or Limits()
        self.dropped = 0
        self.seg = Segmenter(model.threshold_w) if model else None
        # A1
        self._a1_fired = False
        self._a1_over_since: float | None = None
        self._a1_below_since: float | None = None
        # A2 / A5 relay
        self.relay_on: bool | None = None
        self.relay_off_commanded = False
        self._relay_known: bool | None = None  # last on/off seen, kept through gaps
        self._gap_since: float | None = None  # relay unknown since
        self._gap_ha = False  # HA was down during the current gap
        self._a5_pending: Dropout | None = None  # came back off, within the grace time
        self.dropouts: list[Dropout] = []  # observations for the caller to collect
        # A3
        self._failed: list[float] = []
        self._a3_alarmed = False
        self._a3_pending: float | None = None  # first failed start awaiting a normal run
        # N1 / N2: one finding per severity per segment
        self._seg_fired: dict[tuple[float, str], Severity] = {}
        # N4
        self._n4_since: float | None = None
        self._n4_last: float | None = None

    # ---- inputs -------------------------------------------------------------

    def feed(self, ts: float, watts: float | None) -> list[Finding]:
        if watts is not None and self._insane(watts):
            self.dropped += 1
            return []
        out: list[Finding] = []
        if watts is not None:
            out += self._a1(ts, watts)
        if self.seg is not None and self.cfg.profile == Profile.CYCLING:
            for closed in self.seg.feed(ts, watts):
                out += self._on_closed(closed)
            out += self._a3_recovered(ts)
            self._n4_track(ts, watts)
        out += self.tick(ts)
        return out

    def feed_relay(self, ts: float, on: bool | None, commanded: bool) -> list[Finding]:
        """Relay state change. `commanded` = caused by an HA service call.

        `None` (unavailable) starts a gap. The last real state is kept through it,
        so a plug that loses power and boots into `off` is still noticed (A5).
        """
        if on is None:
            self._gap_since = self._gap_since or ts
            self.relay_on = None
            return []
        out: list[Finding] = []
        was = self.relay_on
        back = (Dropout(ts, "off" if on is False else "on", ts - self._gap_since, self._gap_ha)
                if self._gap_since is not None and self._relay_known is True else None)
        self.relay_on = on
        if on is False:
            self.relay_off_commanded = commanded
            if was is True and not commanded:
                out.append(Finding("A2", Severity.ALARM, ts,
                                   f"{self.cfg.name}: the plug switched itself off "
                                   "(overload protection, its button, or a power cut)",
                                   {"offer": ["turn_back_on", "off_on_purpose"]}))
            elif back is not None and not commanded:
                self._a5_pending = back
        else:
            self.relay_off_commanded = False
            pending, self._a5_pending = self._a5_pending, None
            if pending is not None:
                # Booted into off, then on: by itself it restores power; turned on
                # from HA it didn't.
                self.dropouts.append(Dropout(pending.ts, "off" if commanded else "on",
                                             pending.gap_s, pending.ha_down))
            elif back is not None and not commanded and not back.ha_down:
                # After an HA restart the relay is simply still on: that says nothing
                # about the plug, so only gaps seen live count.
                self.dropouts.append(back)
        self._relay_known = on
        self._gap_since, self._gap_ha = None, False
        return out

    def feed_ha_down(self, ts: float) -> None:
        """Home Assistant was stopped at `ts`: the relay is unknown until it reports."""
        self._gap_since = self._gap_since or ts
        self._gap_ha = True
        self.relay_on = None

    def tick(self, now: float) -> list[Finding]:
        out = self._a1_sustained(now) + self._a5(now)
        if self.seg is None or self.cfg.profile != Profile.CYCLING:
            return out
        out += self._a3_unrecovered(now)
        cur = self.seg.current
        if cur is None or not self.seg.is_known:
            return out
        dur = self.seg.known_duration(now)
        if cur.state == RUN:
            out += self._n1(now, cur, dur)
        elif cur.state == IDLE and self.relay_on is not False:
            out += self._n2(now, cur, dur)
            out += self._n4(now)
        return out

    # ---- helpers ------------------------------------------------------------

    def _insane(self, watts: float) -> bool:
        if watts < 0 or watts > self.lim.sanity_abs_w:
            return True
        r = self.cfg.rating_w
        return r is not None and watts > r * self.lim.sanity_factor

    def _once(self, cur: Segment, sev: Severity) -> bool:
        key = (cur.start, sev.value)
        if key in self._seg_fired:
            return False
        self._seg_fired[key] = sev
        return True

    # ---- A1 plug rating -----------------------------------------------------

    def _a1(self, ts: float, watts: float) -> list[Finding]:
        r = self.cfg.rating_w
        if r is None:
            return []
        below = self._a1_below_since
        if self._a1_fired and below is not None and ts - below >= self.lim.a1_rearm_s:
            self._a1_fired = False  # the previous reading held low long enough
        if watts >= r * self.lim.a1_sustain_frac:
            self._a1_over_since = self._a1_over_since or ts
        else:
            self._a1_over_since = None
        if watts < r * self.lim.a1_rearm_frac:
            self._a1_below_since = self._a1_below_since or ts
        else:
            self._a1_below_since = None
        if watts > r and not self._a1_fired:
            self._a1_fired = True
            return [Finding("A1", Severity.ALARM, ts,
                            f"{self.cfg.name}: {watts:.0f} W is over the plug's {r:.0f} W rating",
                            {"watts": watts, "offer": ["cut_power", "will_check"]})]
        return []

    def _a1_sustained(self, now: float) -> list[Finding]:
        since = self._a1_over_since
        if since is None or self._a1_fired or now - since < self.lim.a1_sustain_s:
            return []
        self._a1_fired = True
        r = self.cfg.rating_w
        return [Finding("A1", Severity.ALARM, now,
                        f"{self.cfg.name}: above 90% of the plug's {r:.0f} W rating "
                        f"for {now - since:.0f} s", {"offer": ["cut_power", "will_check"]})]

    # ---- A5 came back off after a dropout --------------------------------------

    def _a5(self, now: float) -> list[Finding]:
        p = self._a5_pending
        if p is None or now - p.ts < self.lim.a5_grace_s or self.relay_on is None:
            return []  # dropped out again: keep waiting for a real state
        self._a5_pending = None
        self.dropouts.append(p)
        why = ("while Home Assistant was down" if p.ha_down
               else f"after {p.gap_s / 60:.0f} min out of contact")
        return [Finding("A5", Severity.ALARM, now,
                        f"{self.cfg.name}: the plug came back switched off {why}. "
                        "A power cut? It does not restore power by itself.",
                        {"gap_s": p.gap_s, "ha_down": p.ha_down,
                         "offer": ["turn_back_on", "off_on_purpose"]})]

    # ---- A3 failed starts ---------------------------------------------------

    def _is_failed_start(self, s: Segment) -> bool:
        m = self.model
        return (s.state == RUN and s.end_reason == "change" and s.known_s <= self.lim.a3_max_s
                and all(w > self.lim.a3_factor * m.run_w for _, w in s.samples))

    def _on_closed(self, s: Segment) -> list[Finding]:
        if not self._is_failed_start(s):
            return []
        self._failed = [t for t in self._failed if s.start - t <= self.lim.a3_window_s]
        self._failed.append(s.start)
        if len(self._failed) >= self.lim.a3_alarm_count and not self._a3_alarmed:
            self._a3_alarmed = True
            self._a3_pending = None
            return [Finding("A3", Severity.ALARM, s.end,
                            f"{self.cfg.name}: {len(self._failed)} failed starts in "
                            f"{(s.start - self._failed[0]) / 60:.0f} min "
                            f"(peak {s.max_w:.0f} W, normally runs at {self.model.run_w:.0f} W)",
                            {"failed_starts": list(self._failed),
                             "offer": ["cut_power", "will_check"]})]
        if self._a3_pending is None and not self._a3_alarmed:
            self._a3_pending = s.start
        return []

    def _a3_recovered(self, ts: float) -> list[Finding]:
        """A normal run after a single failed start: a notice."""
        cur = self.seg.current
        if self._a3_pending is None or cur is None or cur.state != RUN:
            return []
        if not any(w <= self.lim.a3_factor * self.model.run_w for _, w in cur.samples):
            return []
        first = self._a3_pending
        self._a3_pending = None
        return [Finding("A3", Severity.NOTICE, ts,
                        f"{self.cfg.name}: a failed start, then a normal start "
                        f"{(cur.start - first) / 60:.0f} min later",
                        {"failed_start": first, "offer": ["one_off", "will_check"]})]

    def _a3_unrecovered(self, now: float) -> list[Finding]:
        out = []
        if self._a3_pending is not None and now - self._a3_pending > self.lim.a3_recover_s:
            first = self._a3_pending
            self._a3_pending = None
            out.append(Finding("A3", Severity.NOTICE, now,
                               f"{self.cfg.name}: a failed start and no normal start since",
                               {"failed_start": first, "offer": ["will_check", "one_off"]}))
        if self._a3_alarmed and self._failed and now - self._failed[-1] > self.lim.a3_window_s:
            self._a3_alarmed = False  # episode over; a new one can alarm again
            self._failed = []
        return out

    # ---- N1 long run / N2 dead device ---------------------------------------

    def _n1(self, now: float, cur: Segment, dur: float) -> list[Finding]:
        m, lim = self.model, self.lim
        notice_at = max(lim.n1_factor * m.max_run_s, m.p99_run_s + lim.n1_margin_s)
        elevated = cur.median_w > lim.n1_elevated * m.run_w
        alarm = dur >= lim.n1_alarm_factor * m.max_run_s or (
            elevated and dur >= max(notice_at, lim.n1_elevated_after_s))
        sev = Severity.ALARM if alarm else Severity.NOTICE if dur >= notice_at else None
        if sev is None or not self._once(cur, sev):
            return []
        if sev == Severity.ALARM:
            self._seg_fired[(cur.start, Severity.NOTICE.value)] = sev
        return [Finding("N1", sev, now,
                        f"{self.cfg.name}: running for {dur / 60:.0f} min at "
                        f"{cur.median_w:.0f} W (longest normal run {m.max_run_s / 60:.0f} min "
                        f"at {m.run_w:.0f} W)",
                        {"run_start": cur.start, "duration_s": dur, "median_w": cur.median_w,
                         "offer": ["will_check", "new_normal", "something_new", "one_off"]})]

    def _n4_limit(self) -> float:
        m = self.model
        return max(m.pause_level_p90, m.idle_w) + self.lim.n4_margin_w

    def _n4_track(self, ts: float, watts: float | None) -> None:
        cur = self.seg.current
        if watts is None:
            return  # unknown: keep the current state, don't count the gap below
        if cur is not None and cur.state == IDLE and watts >= self._n4_limit():
            self._n4_since = self._n4_since or ts
        else:
            self._n4_since = None

    def _n4(self, now: float) -> list[Finding]:
        """Drawing clearly more than usual between runs: a light on, door ajar."""
        since = self._n4_since
        if since is None or now - since < self.lim.n4_hold_s:
            return []
        if self._n4_last is not None and now - self._n4_last < self.lim.n4_cooldown_s:
            return []
        self._n4_last = now
        w = self.seg.current.samples[-1][1]
        return [Finding("N4", Severity.NOTICE, now,
                        f"{self.cfg.name}: drawing {w:.0f} W between runs for "
                        f"{(now - since) / 60:.0f} min (normally ~{self.model.pause_level_p90:.0f}"
                        " W). A door ajar or a light left on?",
                        {"since": since, "watts": w,
                         "offer": ["will_check", "one_off", "new_normal"]})]

    def _n2(self, now: float, cur: Segment, dur: float) -> list[Finding]:
        m, lim = self.model, self.lim
        limit = max(lim.n2_factor * m.max_pause_s, lim.n2_floor_s)
        if self.cfg.critical:
            # A fridge or freezer must keep starting, whatever the weather.
            quiet, what = dur, "no start"
        else:
            # Demand-driven (a dehumidifier in dry air): a long pause is normal as long as
            # it shows signs of life. Count only the time since the last one.
            life = [t for t, w in cur.samples if w >= m.idle_w + lim.n2_life_w]
            quiet = min(dur, now - life[-1]) if life else dur
            what = "no start and no sign of life"
        if quiet < limit:
            return []
        sev = Severity.ALARM if self.cfg.critical else Severity.NOTICE
        if not self._once(cur, sev):
            return []
        return [Finding("N2", sev, now,
                        f"{self.cfg.name}: {what} for {quiet / 60:.0f} min "
                        f"(longest normal pause {m.max_pause_s / 60:.0f} min)",
                        {"pause_start": cur.start, "duration_s": dur,
                         "offer": ["off_on_purpose", "will_check", "turn_back_on"]})]
