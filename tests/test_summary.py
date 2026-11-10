"""Which plugs the setup picker offers and pre-ticks."""

from custom_components.appliance_guard.model import summarize


def _readings(levels: list[tuple[float, float]], start: float = 0.0):
    """[(watts, seconds)] -> readings, plus a final reading to close the last one."""
    out, t = [], start
    for w, secs in levels:
        out.append((t, w))
        t += secs
    out.append((t, levels[-1][0]))
    return out


def test_charger_is_not_a_plug():
    s = summarize(_readings([(0, 86400), (7400, 3600), (0, 86400)]))
    assert not s.socket
    assert not s.watch


def test_one_spike_over_socket_limit_is_still_a_plug():
    s = summarize(_readings([(5, 86400), (3900, 20), (2000, 600), (5, 86400)]))
    assert s.socket
    assert s.watch  # heavy, rating unknown


def test_kettle_near_rating_is_a_plug():
    s = summarize(_readings([(0, 86400), (3302, 240), (0, 86400)]), rating_w=2500)
    assert s.socket and s.watch


def test_start_spike_is_named_and_not_compared_to_the_rating():
    # a compressor-ish start: 1 reading at 1600 W for 2 s, then ~100 W
    s = summarize(_readings([(0, 3600), (1600, 2), (100, 1200), (0, 86400 * 2)]),
                  rating_w=2500, now=86400 * 1.5)
    assert "start spikes up to 1600 W (yesterday), otherwise at most 100 W" in s.text
    assert not s.watch


def test_held_peak_says_when():
    s = summarize(_readings([(0, 3600), (2100, 600), (0, 3600)]), rating_w=2500,
                  now=4 * 86400)
    assert "peak 2100 W (4 days ago), close to the plug's 2500 W" in s.text


def _held_brute(readings):
    from custom_components.appliance_guard.model import HELD_S, time_weighted
    pairs, best = time_weighted(readings), 0.0
    for i in range(len(pairs)):
        acc, low = 0.0, float("inf")
        for w, secs in pairs[i:]:
            low, acc = min(low, w), acc + secs
            if acc >= HELD_S:
                best = max(best, low)
                break
    return best


def test_held_matches_brute_force():
    import random

    from custom_components.appliance_guard.model import _held
    rng = random.Random(4)
    for _ in range(200):
        levels = [(rng.choice([0, 5, 90, 400, 1500]) + rng.random(),
                   rng.choice([1, 5, 10, 29, 31, 60]))
                  for _ in range(rng.randint(1, 60))]
        readings = _readings(levels)
        if rng.random() < 0.3:
            readings.insert(rng.randrange(len(readings)), (readings[0][0] + 1, None))
            readings.sort(key=lambda r: r[0])
        assert _held(readings) == _held_brute(readings)


def test_summary_of_a_chatty_plug_is_fast():
    """10 days of a reading every 2 s: the setup scan must not hang on it."""
    import time

    levels = [(90 + (i % 7), 2) if (i // 600) % 2 else (1, 2) for i in range(432_000)]
    t = time.perf_counter()
    summarize(_readings(levels), now=864_000)
    assert time.perf_counter() - t < 20
