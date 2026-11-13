# Appliance Guard: design

Appliance Guard watches smart plugs' power readings and tells you when an
appliance or its plug behaves as if something is wrong. It answers "does this
look broken?", not "what is this appliance doing?".

The figures below come from one real installation: 13 watched plugs, 10 days of
raw recorder history, and hourly long-term statistics back to Dec 2024. The
replay fixtures in `tests/fixtures` are taken from it.

## 1. Scope

**What it is for.** Appliances whose failure is costly and goes unnoticed:
fridges, freezers, network gear, servers. And the plugs themselves, which can
overload, switch themselves off, or not restore power after an outage.

**What it is not for.** Appliances people start by hand (kettles, washers,
desks). They have no "normal" to deviate from, and when they fail someone is
standing next to them. These plugs only get the plug checks.

**Measure of success: the false-alarm rate.** A plug that raises a false alarm
more than about once a year gets muted, and then it protects nothing. Every
check below is in because it is near-binary or held up against real data. Two
checks that didn't are left out (section 7).

**Power is a proxy.** A thermometer is a more direct signal for cold storage.
The guard's edge is earlier warning (a stalled compressor shows before the
cabinet warms up) and appliances that have no meaningful temperature.

**Prior art.** Existing tools either learn slowly and report drift (CircuitSetup
Energy Analyzer, after 7–14 days), check peaks only when a cycle completes
(Power Profiler, and a stalled motor never completes), track running/done
states (WashData and similar), or need rules set by hand (Emporia, Sense).
A plug's own overload protection trips silently.

## 2. Profiles and the critical flag

Each watched plug has a **profile**, suggested from its history at setup:

| Profile | Examples | Extra checks |
|---|---|---|
| `cycling` | fridge, freezer, dehumidifier | failed starts; not starting if critical |
| `always_on` | network gear, servers | stopped drawing; switched off from HA if critical |
| `on_demand` | everything else | plug checks only |

**Critical** means "must keep running". Suggested for names containing fridge,
freezer, kyl or frys.

**Only Home Assistant switches this plug** (`ha_only`) says that nothing else
(its button, the vendor app, a voice assistant) switches the relay. It defaults
to the critical flag. See A2 for why.

## 3. Checks

| Check | Applies to | Severity | Needs |
|---|---|---|---|
| A1 Over the plug's rating | every plug | alarm | a rating |
| A2 Switched itself off | every plug | alarm | relay, `ha_only` |
| A5 Came back off after a dropout | every plug | alarm | relay |
| A3 Failed starts | `cycling` | notice / alarm | learned levels |
| N2 Not starting | `cycling`, critical | alarm | learned pauses |
| S2 Stopped drawing power | `always_on` | alarm if critical, else notice | learned floor |
| N5 Switched off from HA | `always_on`, critical | notice | relay |

A1, A2, A3 and A5 are **safety** checks: their alarms come through even when the
plug is paused or snoozed. Everything else, including a critical fridge's "not
starting" alarm, waits.

**A1 Over the plug's rating.** An alarm when a reading is above the rating, or
at least 90 % of it for 60 s. It re-arms after 5 min below 80 %.
- A kitchen plug feeding a coffee brewer and a kettle hit 3302 W on a 2500 W
  Shelly Plug S. Dishwasher and laundry plugs routinely draw about 2000 W.
- The rating is guessed only for models sold in one rating (Shelly Plug S
  2500 W, Aqara `lumi.plug.maeu01` 2300 W, Shelly Power Strip 4 Gen4 2760 W
  per socket). TS011F plugs come as 10 A and 16 A, so no guess: a blanket
  3680 W would keep a 10 A plug silent up to 3680 W.

**A2 Switched itself off.** The relay goes from on to off and no HA service call
explains it: the state change has no user or parent context and matches no
call seen in the last 10 s. That is overload protection or a power cut.
- Real case: a plug read at least 2479 W, then exactly 0 W for 22 h with its
  relay off, most likely its overload protection. It was switched back on
  without anyone suspecting a trip.
- **Only with `ha_only`.** A button press or the vendor app looks exactly the
  same. Replaying 10 days, the 3D printer's outlet would have raised 10 false
  alarms.
- **On by default for critical plugs**, because "not starting" is silent while
  the relay is off. For a fridge, A2 is what catches its plug tripping.
- Answers: Turn back on; Off on purpose; "I switch this plug by hand too",
  which turns `ha_only` off for that plug.

**A5 Came back off after a dropout.** The relay was on, the plug lost contact
(unavailable, or HA down), and it came back off with no HA command explaining
it. An alarm if it is still off 5 min later: some plugs report off for a moment
while booting.
- Cheap plugs often have no reliable "restore after power loss" setting, and
  a firmware update or re-pair can change it. So the guard assumes any plug may
  come back off.
- The relay's last real state is kept through every gap and across HA
  restarts.
- What each plug did after its dropouts is recorded (sensor *After a dropout*).
  One "came back off" settles it. "Came back on" proves little, since a radio
  dropout also looks like that. In the reference data, all 22–66 gaps per plug
  came back on, and a Zigbee coordinator reload showed 7 plugs dropping and
  returning in the same second.

**A3 Failed starts** (`cycling`). A start whose draw never settles: every
reading above 2.5× the learned running level, back below the threshold within
120 s, never at the running level in between. A single reading counts, since
a sparse plug may report one value per burst.
- One failed start, then a normal start within 15 min: a notice. One with no
  normal start after 15 min: a notice. 3 or more within 30 min: an alarm,
  offering Cut power.
- Real cases: a fridge stalled after a mains blip (8 readings at 824–899 W
  against a 90 W run, then a normal start 8.5 min later), and a dehumidifier
  that failed for 5 hours with repeated ~1150 W bursts, noticed by smell.
- Normal starts don't trigger it: a compressor's inrush is one reading
  (600–1615 W) followed by the running level. A defrost heater (160 W for
  12 min) is too low and too long.

**N2 Not starting** (`cycling`, critical only). No start for
`max(1.5 × longest normal pause, 30 min)`. Unavailable time doesn't count, and
it isn't checked while the relay is off.
- The most valuable check for cold storage. Longest normal pauses learned on
  the reference install: freezer 23 min (an alarm after 35 min), fridges 61 and
  131 min.
- **Critical only.** An appliance that runs on demand can rightly rest for
  hours: a dehumidifier in dry air ran no compressor for 9 h, sampling the air
  with a 1-minute fan run every 63 min. Its failure is cheap, and failed starts
  still cover it.
- **No seasonal adjustment.** Fridges and freezers are rated for rooms above
  about 10–16 °C, so a cold room making them stop starting is out of spec, not
  a case to design for.
- Answers: Off on purpose; I'll check; Turn back on; "A different appliance is
  plugged in".

**S2 Stopped drawing power** (`always_on`). Below half the learned floor for
5 min while the relay isn't off.
- The floor is the time-weighted 5th percentile of 10 days, so a reboot's few
  seconds at 0 W don't move it. Reference floors: network 70 W, servers 44 W,
  media converter 3 W.
- Unknown readings stop the clock: a plug out of contact is not a stop.
- A floor under 2 W, or less than a day of history, leaves S2 off: a stop
  can't be told apart from a quiet load.

**N5 Switched off from HA** (`always_on`, critical). A notice when the relay is
switched off by an HA service call, in case it was a mistaken dashboard tap or
an automation bug. Set Off on purpose first to switch it off quietly.

**Keep powered** (option). After A2 or A5, wait 10 s, turn the relay back on and
check 60 s later, then tell the user. Meant for network gear, whose alarm can't
reach a phone while the network is down. Because it overrides a plug that may
have protected itself:
- it needs a rating and `ha_only`;
- it doesn't act if the plug drew 80 % of its rating or more in the last 2 min
  (it may have tripped before a reading over the rating arrived), or within
  10 min of an A1;
- after 3 tries in an hour it gives up with an alarm, and waits until that
  alarm is answered, across restarts too.

## 4. Reading the data

- **A reading holds until the next one.** Plugs report on change. The
  recorder stores changes only, with gaps of up to 3 h when steady.
- **Unknown is unknown.** Unavailable time is never counted as running or
  pausing. Zigbee and Tuya plugs can stay unavailable for up to 2 h after an HA
  restart. Learning marks every `homeassistant_stop` as unknown, because the
  recorder writes nothing while HA is down.
- **Sanity filter.** Readings above 3× the rating or 20 kW are dropped. The
  reference data has one of 34,359,738,368 W.
- **Commanded or not.** A relay change caused by an HA service call carries the
  call's context. This is how A2, A5 and N5 tell HA's own switching from the
  plug's.

## 5. Learning

At setup the guard learns from the last 10 days of raw history in the
recorder. There is no learning week.

**Idle and running levels** (`cycling`): a time-weighted 2-means split on
`log(1 + W)`, below the 99th percentile. The run threshold is halfway between
the two levels.
- Why log: a freezer idles at 1.4 W and runs 85 % of the time anywhere from 33
  to 90 W. Split in watts, that running range was split in two (26.5 / 44.8 W)
  and the freezer was never learned. On a log scale, idle and running are far
  apart.

**What is kept.** What is learned is stored and kept until the user says
otherwise. It isn't relearned daily, so a fault can't become "normal" by
itself.
- Relearning every day from the maximum of the last 10 days learns everything:
  a freezer switched off for 31 h would get a "not starting" limit of 46.5 h for
  the next 10 days, and the dehumidifier's 16-hour stall became its "longest
  normal run".
- Learning changes only on Relearn (the last 10 days), on "A different
  appliance is plugged in" (learn from that moment, retried hourly, status
  `learning`), or when the profile changes.

**What learning leaves out:**
- time the relay was off;
- unknown time;
- pauses longer than 3× the 95th percentile (a day unplugged). A tighter cut
  (2× the 90th) was rejected: an inverter fridge really does pause up to
  165 min against a 95th percentile of 79 min.

**Remaining gap.** A fault that was already going on for most of the 10 days
before setup is learned as normal.

**Enough history.** `cycling` needs 20 complete runs, the same bar at setup and
afterwards. `always_on` needs a day.

## 6. Restarts

HA restarted 27 times in 10 days on the reference install. Each time, the guard
replays the last 12 h of readings silently, so runs and pauses already in
progress are known.

- Open findings, their phone-repeat count, Snooze and Keep powered's state are
  stored and come back as the same Repairs issues, without calling the phone
  again.
- What was already raised for each episode still going on (a pause, a stop)
  is stored too. After the replay, a problem nobody was told about is raised.
  One already raised or answered is not raised again.
- A replay that begins partway through an episode still matches it when the two
  overlap in time.

## 7. Left out, and why

| Left out | Why |
|---|---|
| Running far longer than normal | Defrost, a door left open, warm groceries and summer all look like a long run. The 10-day replay had no true positives |
| More power between runs | A fridge's LED is under its 10 W margin, so "door ajar" doesn't show. In the replay it fired 3 times, each time on a light sharing the fridge's plug |
| Not starting for non-critical appliances | Demand-driven appliances rest for hours (the dry night above). Their failure is cheap, and failed starts still cover them |
| Seasonal learning | See N2. Long-term statistics stay out of learning |
| Heaters told apart from compressors at setup | A start-spike rule was tried: one fridge spikes on 87 % of runs and the dehumidifier on 99 %, but an inverter fridge never does (0 %). The rule would set a critical fridge to on demand. A heater set to `cycling` only gets failed starts, which it can't trigger |
| Several plugs dropping at once | Seen once (both fridges and the media converter at 0 W for 7–15 h). Not built: each plug's own checks still fire |
| Shared loads | One plug feeding a fridge and a light: the light now raises nothing, since "more power between runs" is gone |
| Stuck heater, fridge never stopping, door ajar | They look normal at the plug (README, Limitations) |

## 8. Answers

Every finding is a Repairs issue whose Fix flow offers the answers. A phone
notification, if configured, carries the first two answers plus "More…", which
opens Repairs.

| Answer | Effect |
|---|---|
| Off on purpose | Mode `off_on_purpose`. "Not starting", "stopped drawing", "switched itself off", "came back off" and "switched off from HA" go quiet. Ends after 5 normal runs (`cycling`), or for other profiles when switched back on (a real off→on) or drawing again: 5 W, or half the learned floor for `always_on`, so a 3 W load lapses too |
| A different appliance is plugged in | Forgets what was learned and learns from now on |
| I switch this plug by hand too | Turns off `ha_only` for this plug |
| Turn back on / Cut power | Switches the relay. Cut power also sets Off on purpose |
| One-off / I'll check | Closes the finding. The same episode stays quiet |
| Snooze 1 h / 24 h | Only safety alarms come through for that long |

An unanswered alarm is sent again every 15 min for the first hour, then hourly,
until it is answered. Answering it anywhere clears it from every phone.

**Paused and Disabled** are modes the user sets on purpose, and they don't
expire. The status sensor's `checks` attribute lists every check that applies
to the plug, as `watching` or the reason it isn't: no rating set, not critical,
learning, off on purpose, paused, snoozed, disabled. So protection can't
disappear unnoticed.

## 9. Setup

*Add integration* finds every power sensor on a device that also has a relay
switch, reads 10 days of history for each, and lists them, one line per plug.
Pre-ticked:
- plugs that cycle like a compressor: at least 4 starts a day, never off
  longer than 4 h, and 20 complete runs;
- names that sound like a fridge or freezer;
- plugs whose draw held for 30 s or more reached 80 % of their rating (or
  1800 W when the rating is unknown). Start spikes don't count: plugs don't
  trip on them.

Power sensors that drew more than a socket can deliver (3.7 kW) for 5 min are
left out: chargers, inverters and meters also sit next to a switch. Each
ticked plug becomes its own config entry with the guessed settings, editable
under Configure.

## 10. Architecture and testing

The logic is plain Python with no Home Assistant imports. A thin runtime
connects it to HA.

```
custom_components/appliance_guard/
  episodes.py   run/idle segmentation; unknown gaps
  model.py      learning (levels, pauses, floor) and the setup summary
  detector.py   the checks: readings and relay changes in, findings out
  learn.py      recorder queries
  watcher.py    HA runtime: listeners, context matching, Store, keep powered
  repairs.py    the Fix flow
  config_flow.py, sensor.py, binary_sensor.py, select.py, button.py, event.py
```

Tests:
- `test_replay.py` replays the real fixtures through the detector: the stall
  and failed-start cases, normal starts, unavailable gaps, the dry night, the
  shared light, printer warm-ups, dropouts and restarts.
- `test_model.py` covers learning, including the outlier, relay-off and log
  split cases.
- `test_resume.py` covers what survives a replay.
- `test_runtime.py` runs the integration inside Home Assistant: issues, answers,
  reloads, setup, Keep powered.

The public fixtures hold only the replayed raw readings, without entity ids.

## 11. Fuse Guard

The two are separate integrations: Fuse Guard protects the main fuse, Appliance
Guard protects appliances and plugs. Fuse Guard switches plugs through HA
service calls, so "switched itself off" never fires on them. A compressor
restarted 85 s after stopping can stall (the failed-start case above), so any
load shedding of compressor loads should keep them off for at least 5 min.
