# <img alt="" src="https://raw.githubusercontent.com/ulfben/ha-appliance-guard/main/custom_components/appliance_guard/brand/icon@2x.png" height="48" align="top"> Appliance Guard

[![Validate](https://github.com/ulfben/ha-appliance-guard/actions/workflows/validate.yml/badge.svg?branch=main)](https://github.com/ulfben/ha-appliance-guard/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/ulfben/ha-appliance-guard)](https://github.com/ulfben/ha-appliance-guard/releases/latest)
[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)

A Home Assistant integration that watches appliances on power-measuring smart
plugs and tells you when one behaves as if it's broken.

A fridge that stopped cooling, a dehumidifier whose compressor keeps failing
to start, a heater drawing more than its plug can take, a plug that never
came back on after a power cut: these usually go unnoticed until something
smells, melts or thaws. Appliance Guard learns what normal looks like for
each appliance from its own history, and alerts you on your phone when it
sees one of a few clear signs of trouble.

- **Few, clear alarms.** It only reports things that are rarely innocent:
  over the plug's rating, a plug that switched itself off, failed compressor
  starts, a fridge that stops starting. No vague "unusual usage" scores.
- **Learns from history, starts right away.** It reads the last 10 days from
  the recorder, so there is no learning week and nothing to tune.
- **Finds your plugs for you.** Setup lists the smart plugs it finds, sums up
  each one's last 10 days, and pre-ticks the ones worth watching.
- **Answer in one tap.** Every alert comes with answers such as "It's off on
  purpose", "Something new is plugged in" or "Cut power", from the
  notification or from Settings → Repairs. Each answer changes what it
  watches for, and most lapse by themselves.
- **Assumes cheap plugs.** Many plugs don't reliably restore power after an
  outage. Appliance Guard notices one that came back switched off, and keeps
  a record of how each plug behaved after its dropouts.

> [!IMPORTANT]
> Appliance Guard notices problems and tells you; it doesn't prevent them.
> It can switch a plug off when you tap **Cut power**, but it never does so
> by itself. A plug's own overload protection and your fuses still do the
> protecting, and nothing is watched while Home Assistant is down.

## Requirements

- Home Assistant 2025.6 or newer, with the recorder (on by default).
- Smart plugs (or any device) with a **power sensor in watts**. A **relay
  switch** on the same device is optional, but needed for the checks that
  involve switching off and for **Cut power** / **Turn back on**.
- For phone alerts: the Home Assistant Companion app. Without it, alerts
  show under Settings → Repairs.

## Installation

### HACS

Appliance Guard isn't in the HACS default list yet. Add it as a custom
repository: HACS → ⋮ → **Custom repositories** → add
`https://github.com/ulfben/ha-appliance-guard` as an **Integration**. Then
search for **Appliance Guard**, install it, and restart Home Assistant.

### Manual

Copy `custom_components/appliance_guard` into your
`config/custom_components/` folder and restart Home Assistant.

## Setup

Settings → Devices & services → **Add integration** → **Appliance Guard**,
then **Choose from the plugs found**.

1. **Scanning.** It looks for devices that have both a power sensor and a
   switch, and reads 10 days of history for each. With many plugs this can
   take a minute.
2. **Pick the plugs to watch.** Each plug gets a one-line summary, for
   example *"Basement fridge: cycles like a compressor: ~90 W, 17 starts a
   day; start spikes up to 1615 W (5 days ago), otherwise at most 862 W"*.
   Pre-ticked are appliances that cycle like a compressor (fridges,
   freezers, dehumidifiers), anything named like a fridge or freezer, and
   plugs that have drawn close to their rating. Tick anything else you want
   watched: a 3D printer, a heater, the network gear.
3. **Pick the phones** to notify. They're used for every plug picked here.

Each ticked plug becomes its own entry, with settings guessed from the plug
and its history. Then open **Configure** on each one and check:

- **Plug rating:** the most the plug is made to switch, per socket, from its
  label or spec sheet. It's filled in for a few known models only. Rated in
  amps? Multiply by 230: 10 A = 2300 W, 12 A = 2760 W, 16 A = 3680 W.
- **Type of appliance:** *cycling* (fridge, freezer, dehumidifier),
  *always on* (network gear, servers) or *on demand* (everything used now
  and then). Cycling and always-on appliances get extra checks, below.
- **Critical:** for a fridge, freezer or the network. For a cycling
  appliance, a long pause becomes an alarm. Leave it off for appliances that
  only run when needed, such as a dehumidifier: then a long pause is fine as
  long as the appliance shows signs of life, like its fan sampling the air.
  For an always-on appliance, stopping is an alarm, and switching it off
  from Home Assistant gives a notice.
- **Keep powered:** turns the plug back on by itself when it switches itself
  off or comes back off after a power cut, and tells you. It never does so
  right after the plug was over its rating, and gives up after 3 tries in an
  hour. Worth it for network gear: while the network is down, the alarm
  can't reach your phone anyway.
- **Relay switch** and **Phones to notify**.

To watch a sensor that isn't on a smart plug, choose **Pick a power sensor
by hand** instead.

### Testing a new plug

Plugs differ in what they do when power returns, and many cheap ones have
no reliable setting for it. To find out: unplug the plug until Home
Assistant shows it as unavailable, then plug it back in. The plug's **After
a dropout** sensor shows whether it restored power. If it came back off, you
also get the alarm described below.

## What it watches for

**For every plug:**

| Alert | When |
|---|---|
| **Over the plug's rating** | Above the rating, or above 90 % of it for a minute. Needs the rating |
| **Plug switched itself off** | The relay went off without Home Assistant switching it: the plug's overload protection, its button, or a power cut. Needs the relay |
| **Plug came back off after a dropout** | The plug lost contact (a power cut, or Home Assistant restarting) and was still off 5 minutes after it came back. Needs the relay |

**For cycling appliances** (fridges, freezers, dehumidifiers), from what it
learned about this appliance's runs and pauses:

| Alert | When |
|---|---|
| **Failed starts** | Repeated short bursts of high draw that never settle into a normal run: a compressor that can't start |
| **Not starting** | No start for half again as long as the longest normal pause. For critical appliances, an alarm. For others, a notice, and only if it also shows no sign of life (nothing drawn above idle, such as a fan) for that long |
| **Running far longer than normal** | A run much longer than any it has learned |
| **Drawing more than usual between runs** | A higher draw than usual between runs for 15 minutes: a door ajar, or a light left on |

**For always-on appliances** (network gear, servers):

| Alert | When |
|---|---|
| **Stopped drawing power** | Under half its normal minimum for 5 minutes while the plug is on: a dead power supply, or a device that shut down. An alarm if critical, otherwise a notice |
| **Switched off from Home Assistant** | Critical ones only: a notice, in case it was a mistaken tap or an automation. Set **Off on purpose** first to switch it off quietly |

The first four (rating, switched off, came back off, failed starts) are
safety alarms. They come through even while the plug is paused or an alert
is snoozed.

## Answering an alert

Each alert shows up under **Settings → Repairs**, and on the phones you
picked as a notification with buttons. The answers on offer depend on the
alert:

| Answer | What happens |
|---|---|
| It's off on purpose | No "not starting", "stopped drawing" or "came back off" alerts until the appliance runs again |
| Something new is plugged in / This is the new normal | Learns again from history |
| One-off, it's fine / I'll check it | Closes the alert |
| Snooze 1 hour / 24 hours | No notices for this plug for that long |
| Cut power | Switches the plug off and marks it off on purpose |
| Turn the plug back on | Switches the plug on |

## Entities

Each watched plug gets a device, "*name* guard", with:

| Entity | What it shows |
|---|---|
| Status | `ok`, `learning`, `notice`, `alarm`, `off_on_purpose`, `paused` or `disabled` |
| Problem | On while an alarm is open |
| Finding | An event for each alert and answer, for your own automations |
| Mode | **Watching**; **Off on purpose**; **Paused** (only safety alarms come through); **Disabled**. Set it ahead of time: "the basement fridge goes off tomorrow" |
| Relearn | Learns again from the last 10 days |
| Normal running power, Normal power between runs, Longest normal run, Longest normal pause | What it learned (cycling appliances) |
| Normal minimum power | What it learned (always-on appliances) |
| After a dropout | What the plug did after losing contact: `not_seen`, `came_back_on` or `came_back_off`. "Came back off" sticks once seen |

## Limitations

- It watches each plug on its own. Several plugs on one power strip, or one
  fuse, aren't added up.
- A heater stuck on at its normal power looks normal. Only cycling
  appliances get the "running far longer than normal" check.
- What it learns comes from the last 10 days, so a fridge that works harder
  in summer may need **Relearn** when the seasons change.

## Development

```bash
uv venv --python 3.13 && uv pip install -r requirements_test.txt
uv run ruff check . && uv run pytest
```

The checks (`detector.py`) and the learning (`model.py`, `episodes.py`) are
plain Python with no Home Assistant imports. `tests/test_replay.py` replays
real recorder history (`tests/fixtures/reference`) through them. The Home
Assistant test plugin needs POSIX modules; on Windows, run
`python tools/pytest_windows.py` instead of `pytest`.

The design and the reasoning behind each check are in
[docs/DESIGN.md](https://github.com/ulfben/ha-appliance-guard/blob/main/docs/DESIGN.md).

## License

[MIT](https://github.com/ulfben/ha-appliance-guard/blob/main/LICENSE) © Ulf Benjaminsson
