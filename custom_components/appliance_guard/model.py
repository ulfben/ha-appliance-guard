"""Learn a device's normal behaviour from a reading stream.

v0 learns from raw history only (idle/run levels, run and pause lengths). The seasonal
envelope from long-term statistics (DESIGN.md section 5) comes later.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .episodes import IDLE, RUN, Segment, segment_all

MAX_HOLD_S = 3600.0  # a reading weighs at most this long (sparse plugs, restarts)


@dataclass(frozen=True)
class Model:
    idle_w: float
    run_w: float  # typical running level (time-weighted median above the threshold)
    threshold_w: float
    runs: int
    max_run_s: float
    p99_run_s: float
    max_pause_s: float
    p99_pause_s: float
    pause_level_p90: float  # what the device draws between runs, p90 over pauses


def weighted_percentile(pairs: list[tuple[float, float]], q: float) -> float:
    """pairs = (value, weight); q in 0..100."""
    pairs = sorted(p for p in pairs if p[1] > 0)
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        raise ValueError("no weighted values")
    target, acc = total * q / 100.0, 0.0
    for v, w in pairs:
        acc += w
        if acc >= target:
            return v
    return pairs[-1][0]


def percentile(values: list[float], q: float) -> float:
    return weighted_percentile([(v, 1.0) for v in values], q)


def time_weighted(readings: list[tuple[float, float | None]]) -> list[tuple[float, float]]:
    """(watts, seconds held) for every known reading followed by another input."""
    out = []
    for (ts, w), (nts, _) in zip(readings, readings[1:], strict=False):
        if w is not None:
            out.append((w, min(nts - ts, MAX_HOLD_S)))
    return out


def two_levels(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Time-weighted 2-means on values up to the p99 (start surges excluded)."""
    cap = weighted_percentile(pairs, 99)
    pairs = [p for p in pairs if p[0] <= cap]
    lo, hi = weighted_percentile(pairs, 5), weighted_percentile(pairs, 95)
    for _ in range(50):
        mid = (lo + hi) / 2
        a = [(v, w) for v, w in pairs if v < mid]
        b = [(v, w) for v, w in pairs if v >= mid]
        if not a or not b:
            break
        nlo = sum(v * w for v, w in a) / sum(w for _, w in a)
        nhi = sum(v * w for v, w in b) / sum(w for _, w in b)
        if abs(nlo - lo) < 0.01 and abs(nhi - hi) < 0.01:
            break
        lo, hi = nlo, nhi
    return lo, hi


class NotCyclingError(ValueError):
    """The history has no clear idle and running levels."""


class NoSteadyDrawError(ValueError):
    """An always-on load whose history shows no steady draw to watch."""


ALWAYS_ON_MIN_W = 2.0  # below this a stop can't be told from a quiet load
ALWAYS_ON_MIN_KNOWN_S = 86400.0


def learn_floor(readings: list[tuple[float, float | None]]) -> float:
    """The least an always-on load normally draws: its time-weighted 5th percentile.

    Short dips (a reboot, the odd 0 W reading) fall below it and don't count.
    """
    pairs = time_weighted(readings)
    if sum(s for _, s in pairs) < ALWAYS_ON_MIN_KNOWN_S:
        raise NoSteadyDrawError("less than a day of history")
    floor = weighted_percentile(pairs, 5)
    if floor < ALWAYS_ON_MIN_W:
        raise NoSteadyDrawError(
            f"draws under {ALWAYS_ON_MIN_W:.0f} W much of the time, so a stop can't be told apart")
    return floor


def learn_cycling(readings: list[tuple[float, float | None]], min_runs: int = 20) -> Model:
    pairs = time_weighted(readings)
    if not pairs:
        raise NotCyclingError("no readings")
    lo, hi = two_levels(pairs)
    if hi < max(2 * lo, lo + 5):
        raise NotCyclingError(f"levels too close: {lo:.1f} W / {hi:.1f} W")
    threshold = (lo + hi) / 2
    run_w = weighted_percentile([p for p in pairs if p[0] >= threshold], 50)

    segs = [s for s in segment_all(readings, threshold) if s.complete]
    runs = [s.known_s for s in segs if s.state == RUN]
    pauses = [s.known_s for s in segs if s.state == IDLE]
    levels = [segment_level(s) for s in segs if s.state == IDLE and s.known_s >= 300]
    if len(runs) < min_runs or not pauses:
        raise NotCyclingError(f"only {len(runs)} complete runs")
    return Model(
        idle_w=lo,
        run_w=run_w,
        threshold_w=threshold,
        runs=len(runs),
        max_run_s=max(runs),
        p99_run_s=percentile(runs, 99),
        max_pause_s=max(pauses),
        p99_pause_s=percentile(pauses, 99),
        pause_level_p90=percentile(levels, 90) if levels else lo,
    )


def suggest_profile(readings: list[tuple[float, float | None]]) -> str:
    """'cycling', 'always_on' or 'on_demand', from history. Only a suggestion."""
    return summarize(readings).profile


def segment_level(s: Segment) -> float:
    """Time-weighted median power of a closed segment."""
    ends = [t for t, _ in s.samples[1:]] + [s.end]
    return weighted_percentile(
        [(w, max(e - t, 0.001)) for (t, w), e in zip(s.samples, ends, strict=True)], 50)


def _held(readings: list[tuple[float, float | None]]) -> float:
    """The highest draw held without a break for HELD_S (start spikes don't count)."""
    # For each start i, the shortest run of readings i..j lasting HELD_S; the
    # answer is the best minimum over those runs. j only moves forward as i
    # does, so a sliding-window minimum keeps this one pass: plugs can have
    # hundreds of thousands of readings.
    pairs = time_weighted(readings)
    best, acc, j = 0.0, 0.0, 0
    mins: deque[int] = deque()  # indexes in i..j-1, their watts increasing
    for i in range(len(pairs)):
        while j < len(pairs) and acc < HELD_S:
            while mins and pairs[mins[-1]][0] >= pairs[j][0]:
                mins.pop()
            mins.append(j)
            acc += pairs[j][1]
            j += 1
        if acc < HELD_S:
            break
        best = max(best, pairs[mins[0]][0])
        acc -= pairs[i][1]
        if mins[0] == i:
            mins.popleft()
    return best


@dataclass(frozen=True)
class PlugSummary:
    """What a plug's history looks like, for choosing which plugs to watch."""

    profile: str
    text: str
    peak_w: float | None
    watch: bool  # suggested
    socket: bool = True  # False: drew more than a wall socket can deliver, so not a plug


# Compressor-like: starts often and is never off for long. Desks, heaters and media
# centres also have two levels, but rest for many hours.
CYCLING_MIN_STARTS_PER_DAY = 4.0
CYCLING_MAX_PAUSE_S = 4 * 3600.0
NEAR_RATING = 0.8  # a peak above this share of the rating is worth watching
HEAVY_W = 1800.0  # the same, for plugs whose rating is unknown
INSANE_W = 20000.0
# A wall socket gives at most ~3.7 kW (16 A at 230 V). Anything that draws more for
# minutes is a charger, an inverter or a whole-house meter, not a plug.
SOCKET_MAX_W = 3700.0
SOCKET_MAX_OVER_S = 300.0
# A peak shorter than this is a start spike (inrush). Plugs don't trip on those, so the
# rating comparison uses the highest draw held at least this long.
HELD_S = 30.0


def _ago(seconds: float) -> str:
    days = seconds / 86400
    if days < 1:
        return "today"
    if days < 2:
        return "yesterday"
    return f"{days:.0f} days ago"


def summarize(readings: list[tuple[float, float | None]], rating_w: float | None = None,
              critical: bool = False, now: float | None = None) -> PlugSummary:
    """One line about a plug's history, a suggested profile, and whether to watch it.

    Suggested: anything that cycles like a compressor, anything named like a fridge,
    and any plug whose peak comes close to its rating.
    """
    known = [(t, w) for t, w in readings if w is not None and 0 <= w <= INSANE_W]
    if len(known) < 2:
        return PlugSummary("on_demand", "no history yet", None, critical)
    days = max((known[-1][0] - known[0][0]) / 86400, 1 / 24)
    peak_ts, peak = max(known, key=lambda r: (r[1], r[0]))  # latest of equal peaks
    now = known[-1][0] if now is None else now
    profile, parts = "on_demand", []
    try:
        m = learn_cycling(readings, min_runs=10)
        if m.runs / days >= CYCLING_MIN_STARTS_PER_DAY and m.p99_pause_s <= CYCLING_MAX_PAUSE_S:
            profile = "cycling"
            parts.append(f"cycles like a compressor: ~{m.run_w:.0f} W, "
                         f"{m.runs / days:.0f} starts a day")
    except NotCyclingError:
        pass
    if profile != "cycling":
        pairs = time_weighted(readings)
        if pairs:
            p5, p50 = weighted_percentile(pairs, 5), weighted_percentile(pairs, 50)
            if p50 >= 2 and p5 >= 0.5 * p50:
                profile = "always_on"
                parts.append(f"steady ~{p50:.0f} W")
        if profile == "on_demand":
            parts.append("used now and then" if peak >= 5 else "almost no draw")
    held = _held(readings)
    near = held >= NEAR_RATING * rating_w if rating_w else held >= HEAVY_W
    if peak > max(1.5 * held, held + 100):
        peak_text = (f"start spikes up to {peak:.0f} W ({_ago(now - peak_ts)}), "
                     f"otherwise at most {held:.0f} W")
    else:
        peak_text = f"peak {peak:.0f} W ({_ago(now - peak_ts)})"
    if rating_w and near:
        peak_text += f", close to the plug's {rating_w:.0f} W"
    elif not rating_w and near:
        peak_text += ", rating unknown"
    parts.append(peak_text)
    over_s = sum(s for w, s in time_weighted(readings) if SOCKET_MAX_W < w <= INSANE_W)
    socket = over_s < SOCKET_MAX_OVER_S
    return PlugSummary(profile, "; ".join(parts), peak,
                       socket and (profile == "cycling" or critical or near), socket)
