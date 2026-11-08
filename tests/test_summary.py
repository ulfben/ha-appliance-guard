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
