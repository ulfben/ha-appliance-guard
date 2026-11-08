"""Split a power reading stream into run and idle segments.

Readings arrive sparsely and only on change, so a reading holds until the next one.
`None` means unavailable/unknown: that time is never counted as running or idle.
If a device is in the same state on both sides of an unknown gap, the segment
continues (without counting the gap); otherwise the segment closes where the gap began.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

IDLE = "idle"
RUN = "run"

# Why a segment ended / began.
CHANGE = "change"  # a reading crossed the threshold
UNKNOWN = "unknown"  # an unknown gap with a different state on the other side
FIRST = "first"  # first reading ever seen


@dataclass
class Segment:
    state: str
    start: float
    start_reason: str
    last_ts: float
    samples: list[tuple[float, float]] = field(default_factory=list)
    known_s: float = 0.0
    end: float | None = None
    end_reason: str = ""

    @property
    def complete(self) -> bool:
        """Both ends were observed as real transitions, so its length is trustworthy."""
        return self.start_reason == CHANGE and self.end_reason == CHANGE

    @property
    def max_w(self) -> float:
        return max(w for _, w in self.samples)

    @property
    def median_w(self) -> float:
        return median(w for _, w in self.samples)


class Segmenter:
    """Incremental segmentation at a fixed run threshold."""

    def __init__(self, threshold_w: float) -> None:
        self.threshold_w = threshold_w
        self.current: Segment | None = None
        self._known = False  # is the latest input a real reading?
        self._unknown_since: float | None = None

    def state_of(self, watts: float) -> str:
        return RUN if watts >= self.threshold_w else IDLE

    def feed(self, ts: float, watts: float | None) -> list[Segment]:
        """Add one input; return the segments that closed because of it."""
        cur = self.current
        if watts is None:
            if self._known and cur is not None:
                cur.known_s += ts - cur.last_ts
                cur.last_ts = ts
                self._unknown_since = ts
            self._known = False
            return []

        state = self.state_of(watts)
        if cur is None:
            self.current = Segment(state, ts, FIRST, ts, [(ts, watts)])
            self._known = True
            return []

        closed: list[Segment] = []
        if not self._known:  # coming back from an unknown gap
            if state == cur.state:
                cur.last_ts = ts
                cur.samples.append((ts, watts))
            else:
                cur.end, cur.end_reason = self._unknown_since, UNKNOWN
                closed.append(cur)
                self.current = Segment(state, ts, UNKNOWN, ts, [(ts, watts)])
        else:
            cur.known_s += ts - cur.last_ts
            cur.last_ts = ts
            if state == cur.state:
                cur.samples.append((ts, watts))
            else:
                cur.end, cur.end_reason = ts, CHANGE
                closed.append(cur)
                self.current = Segment(state, ts, CHANGE, ts, [(ts, watts)])
        self._known = True
        return closed

    def known_duration(self, now: float) -> float:
        """Observed length of the open segment at `now`, excluding unknown time."""
        cur = self.current
        if cur is None:
            return 0.0
        return cur.known_s + (max(0.0, now - cur.last_ts) if self._known else 0.0)

    @property
    def is_known(self) -> bool:
        return self._known


def segment_all(readings: list[tuple[float, float | None]], threshold_w: float) -> list[Segment]:
    """All closed segments of a finished stream (the open last one is dropped)."""
    seg = Segmenter(threshold_w)
    out: list[Segment] = []
    for ts, w in readings:
        out.extend(seg.feed(ts, w))
    return out
