# Appliance Guard: design

Status: draft, 2026-11-07. Working name "Appliance Guard" / `appliance_guard`
(not yet checked against HACS, core and brands).

## 1. What it does

Appliance Guard watches the power readings of smart plugs. It tells you
when an appliance **behaves like something is wrong**:

- it draws far more than it should;
- it keeps failing to start;
- it runs and never stops;
- it stops running when it never should;
- its plug switches itself off.

Existing tools answer "what is this appliance doing?" or "has it finished?".
This one answers "does this look broken?". That changes the priorities:

- **Fast.** Alarms come within minutes, not at the end of a cycle and not in a
  weekly digest.
- **Useful from day one.** Baselines are learned from the history Home
  Assistant already has, not from a fresh week of watching.
- **Talks back.** A notice comes with a quick way to explain it ("the fridge is
  off on purpose", "I plugged in something new"). Your answer changes what the
  guard does, and each answer lapses by itself when it no longer applies.

### Non-goals (v1)

- Working out which appliance is which from a whole-house meter (NILM). The
  guard only watches individual plugs.
- Power-quality analysis (power factor, harmonics).
- Load shedding. That belongs to Fuse Guard v2 (section 13).
- Energy accounting and cost.

### Prior art checked (2026-11-07)

| Project | What it does | Why it doesn't cover this |
|---|---|---|
| CircuitSetup Energy Analyzer (HACS, testing) | Learned baselines, cycle drift, compressor signature | Needs 7–14+ days. Its findings are "inspection prompts, not fault diagnoses". Aimed at slow drift |
| home-generative-agent Sentinel | Baseline-deviation rules | A large LLM-agent project in which anomaly detection is a side feature |
| Power Profiler | Cycle profiles, peak and energy anomaly checks | Peak and energy are only checked **when the cycle completes**. A stalled motor never completes |
| Appliance Status Monitor, WattWatcher, WashData, Appliance Sensor | Running / idle / done states | No fault detection |
| Emporia, Sense | Per-device "on too long" / "no usage" rules | Rules set by hand. "Unusual consumption" is not shipped. Sense's motor-stall lab was 240 V only |
| Plug overload protection (Shelly, Aqara, Tasmota) | Relay trips above a set power | Crude. It only fires at the plug's limit, and it does so **silently** |

## 2. Reference cases

The design is driven by real events at the reference installation. Each one
becomes a replay test (section 11).

| # | Event | What the data shows | Wanted outcome |
|---|---|---|---|
| R1 | **Dehumidifier stall**, 2026-10-25 06:00–11:00 | Hourly max 1138–1169 W (normal peak ≤ 540 W) and hourly mean 70–130 W for 5 h: repeated short high bursts. Noticed by smell. A possible mains blip in the hour before | **Alarm** within ~15 min, offering "Cut power" |
| R2 | **Plug overload trip**, 2026-08-07 15:00 | ≥ 2479 W, then exactly 0 W for 22 h. The relay was off; the user switched it back on without suspecting anything | **Alarm**: "the plug switched itself off" |
| R3 | **Fridge failed start after a mains blip**, 2026-11-02 15:02 | The compressor stopped at 15:00:47 and restarted 85 s later at 824–899 W for 35 s (8 readings), cut out, then restarted normally 8.5 min later | **Notice**: one failed start that recovered |
| R4 | **Another load on the same plug**, 2026-11-01 10:38, 11-03 16:26, 11-04 16:25–23:12, 11-07 11:24 and 16:44 | 22 W between runs and 111 W runs (89 + 22). The plug also feeds an extension cord with the kitchenette lights: every episode starts within a second of the light turning on. At a 20 W threshold this looked like one 6.5 h run | **No notice** once the light is linked as a shared load (section 3). Unlinked: N4 notice, answered "another load shares this plug" |
| R5 | **Kitchen Island over rating**, 2026-04-22 07:00 | 3302 W on a Shelly Plug S (~2500 W). Coffee brewer and kettle together | **Alarm**: over the plug's rating |
| R6 | **Space heater on a desk plug**, Jan 2026 | ~1100–1845 W for 6–7 h on a plug that normally draws ~70 W | **Notice** answered "something new" or "one-off" |
| R7 | **Radiator setting**, 2026-10-26 | 1818 W once ("high"), ~770 W since ("low") | **Notice**: "new normal" if it repeats |
| R8 | **Basement fridge emptied and off on purpose**, Dec 2025 | 0 W for 31 h / 96 h / 159 h | **Notice** answered "off on purpose", which lapses by itself when the fridge runs again |
| R9 | **Printer warm-up on the Servers plug** | ~1 kW, decaying over ~45 s, whenever the printer prints (64 of 76 burst hours). The printer shares the plug | No notice: too brief for A4, and A3 only applies to `cycling` |
| R10 | **Plugs unavailable after an HA restart** | The Basement Fridge plug stayed unavailable for 2 h after a restart. ~20 blips per plug in 10 days | No alarm. Missing time counts as unknown, never as "off" |
| R11 | **Simultaneous drop**, 2025-11-06 | Both fridges and the Fiberconverter read 0 W for 7–15 h | **One** household-level alert, not three |
| R12 | **Normal compressor starts** | One reading of 600–1615 W, then a steady run | No notice |

## 3. Concepts

- **Watched device**: one power sensor, plus optionally the relay `switch` on
  the same device and its plug rating. The appliance is identified only by what
  you tell the guard.
- **Profile** (suggested from history, editable):
  - `cycling`: fridge, freezer, dehumidifier. The compressor starts and stops
    by itself, so pause and run lengths are meaningful.
  - `always_on`: network gear, servers, fiber converter. Draws within a band
    around the clock.
  - `on_demand`: kettle, washing machine, heater. Runs when someone starts it.
    Only the power and run-length checks apply.
- **Critical**: a `cycling` device whose stopping is an emergency (cold
  storage). It turns a "dead device" finding into an alarm. On by default for
  devices named fridge, freezer, kyl or frys; editable.
- **Shared loads**: other things on the same plug, e.g. lights on an extension
  cord (R4) or a printer next to servers (R9). Each is linked as an entity
  whose `on` state (or rising counter) explains extra draw. The guard learns
  that entity's draw by comparing readings just before and after its changes
  (e.g. +22 W), and subtracts it before running the checks. A link can be
  suggested automatically: an entity whose on/off changes repeatedly line up
  within a few seconds with step changes in the plug's power.
- **Mode**: `watching`, `learning`, `off_on_purpose`, `paused` (until a set
  time) or `disabled`.
- **Finding**: the result of one check (section 6), with a severity:
  - `notice`: unusual, please explain;
  - `alarm`: may be dangerous, act now.

## 4. Reading the data

**Readings come from state changes.** Plugs report sparsely, often only on
change: the medians range from 3 s (Aqara) to about 200 s (the dehumidifier
when steady). The recorder stores changes only. The guard therefore treats a
reading as holding until the next one. Freshness uses `last_reported`, as in
Fuse Guard, so a steady value is not mistaken for silence.

**Sanity filter.**
- Readings that are not numbers are ignored.
- Readings ≥ 3× the plug's rating, or above 20 kW, are dropped and counted in
  diagnostics. (On 2026-08-20 there was a reading of 34,359,738,368 W.)
- Restored states after an HA restart are ignored.

**Unavailable or unknown means unknown.** Time without readings is never
counted as off-time or on-time. The checks that measure time (pause length,
run length) pause while the plug is unavailable. If the plug is unavailable
for longer than a limit (default 30 min outside the restart grace), that is a
finding of its own (S1).

**HA restart grace.** For 15 minutes after HA starts, missing plugs are
expected. Some Zigbee plugs only report again on their next change, which can
take up to 2 h (R10). During the grace period and until the first fresh
reading, the device is `unknown`, not `off`.

**Start detection.** A start is a reading at or above the running threshold
after at least one reading below it. The running threshold is set per device
halfway between the learned idle level and the learned running level. A
compressor's start surge is usually a **single reading** (R12): a lone high
reading followed by a normal running level is a normal start.

## 5. Learning the baseline

### Sources, at setup and when relearning

1. **Long-term statistics** (hourly mean, min and max, kept indefinitely),
   back as far as 13 months. These capture the **seasons** (the LG fridge's
   hourly peak is 54 W in November and ~120 W in July) and the rare normal
   events (printer warm-ups, defrosts).
2. **Raw history** (≈ 10 days). This gives the shape of each run: idle
   level, running level, start surge, run and pause lengths, starts per hour.

From then on, the model keeps updating from live readings with slow
exponential forgetting (a half-life of ~30 days), so it follows gradual change
without learning a fault as normal.

### What is learned per device

| Feature | From | Used by |
|---|---|---|
| Idle level, running level (robust percentiles) | Raw | Start detection, S2 |
| Start surge: distribution of the first-reading peak | Raw | A3 |
| Number of readings above `k`× the running level per start | Raw | A3 |
| Run lengths: p99 and maximum | Raw | N1 |
| Pause lengths: p99 and maximum, excluding unavailable time | Raw | N2 |
| Starts per hour: p99 | Raw | N3 |
| Hourly max and mean envelope **per calendar month** | Long-term statistics | A4, N1 (seasonal) |
| Always-on band | Both | S2 |

**Seasonal envelope.** For a given hour, the expected upper limit is the
highest of:
- the last 30 days' p99.5;
- the p99.5 of the same calendar month ±1 from last year, if that data exists.

So a fridge working hard in July is not flagged against what it drew in
November.

**Not enough history:** `learning` mode. The plug-rating, relay and stall
checks still run, because they need no baseline. The other checks start once
enough has been seen: for `cycling`, 20 complete runs; otherwise 3 days.

**Nothing is learned from:**
- open alarms;
- unanswered notices;
- time spent `off_on_purpose`;
- readings caught by the sanity filter.

## 6. Checks

Initial limits are given below. All of them can be changed under Advanced,
and replay tests pin the defaults.

### Alarms (need no baseline)

**A1 Plug rating.** The plug rating is guessed from the model (Shelly Plug S
~2500 W, Aqara `lumi.plug.maeu01` 2300 W, TS011F 3680 W). The guess is shown
at setup and must be confirmed.
- Alarm when one reading is above 100% of the rating.
- Alarm when power stays above 90% for 60 s or more.
- Catches R5.

**A2 Relay switched off by itself.** The relay goes from `on` to `off` and
the state change's context does not match any HA service call for that entity
in the last 5 s. That means it was switched at the device: the overload
protection tripped, someone pressed the button, or the plug rebooted into
`off`.
- One alarm, with the last 60 s of readings and a **"Turn back on"** button.
- Catches R2.
- Relay changes commanded from HA (an automation, Fuse Guard, a dashboard) are
  never findings.
- A Zigbee plug that loses power briefly may not go unavailable at all, and just
  reports `off` when it rejoins. That lands here, so the text names a power cut
  as one of the possible causes.

**A5 Came back off after a dropout.** Many cheap plugs have no reliable
"restore after power loss" setting, so any plug may come back off after a power
cut. That has to be tested by hand per plug, and a firmware update or a re-pair
can change it again. The relay's last real state is kept through every gap
(unavailable, or HA down):
- **Alarm** when the plug comes back `off`, its last real state was `on`, no HA
  command explains it, and it is still `off` after 5 min. Some plugs report `off`
  for a moment while booting, HA and the network take a while to settle after a
  power cut, and an appliance being off is rarely urgent.
- Across an HA restart: when the integration starts with HA, the relay history
  up to the last `homeassistant_stop` is replayed, and the state the plug comes
  back with counts as new. A relay that was switched off from HA before the
  restart stays quiet.
- Offers **Turn back on** and **Off on purpose**. Quiet in `off_on_purpose`
  mode, and a safety alarm in every other mode.
- **What each plug does after a dropout is recorded** (persisted per plug) and
  shown as the diagnostic sensor *After a dropout*: `not_seen`, `came_back_on`
  or `came_back_off`, with counts and the last time. A dropout that may not have
  been a power cut, such as a radio dropout or an HA restart, says little:
  - "came back on" after a live gap is counted, but proves nothing;
  - gaps that span an HA restart are only recorded when the plug came back off;
  - one "came back off" settles it, so that state sticks.
  The manual test at setup becomes: unplug it until HA shows it unavailable,
  plug it back in, and read the sensor.
- In the reference data, every one of 22–66 gaps per relay came back on.

**A3 Failed starts (stall)** (`cycling` only). A start whose high draw does
not settle:
- **one failed start:** the device rises from idle, stays above
  `2.5 × running level` for every reading, and is back at idle within 120 s,
  with no reading at the normal running level in between. A thermal
  protector trips in about 5–30 s.
- **This must also work with a single reading.** A sparse plug may report
  only one value per burst, so a lone high reading followed by idle counts too.
  At the basement fridge: 8 readings at 824–899 W against a 225 W limit (R3).
  For the old dehumidifier, the hourly figures fit bursts of about 1150 W.
- **Defrost heaters don't trigger it.** The fridge's defrost runs at 160 W for
  about 12 min: it is too long and too low.
- **A normal start doesn't either.** It also begins with a single surge reading
  (600–1615 W), but the next reading is at the running level.
- a single failed start followed by a normal run within 15 min → **notice**
  (catches R3);
- **3 or more failed starts within 30 min → alarm, with "Cut power"** (catches
  R1);
- while the device is stuck in failed starts, the alarm repeats every 15 min
  until it is answered.

A lone surge reading followed by a normal run is a normal start (R12).

**A4 Sustained over-draw.** Power above `1.5 ×` the seasonal envelope's hourly
max for 10 min or more.
- **Notice** at first; **alarm** after 30 min, or when power is also above 70%
  of the plug's rating.
- Catches R6 and R7 (as notices, answered with "something new" or "new
  normal") and a stuck heating element.

### Notices that can become alarms (need a baseline)

**N1 Long run** (`cycling`). A run longer than `max(1.5 × learned maximum
run, learned p99 + 45 min)`.
- Becomes an **alarm** at 3× the learned maximum, or after 2 h when the
  running level is also above 1.2× normal.
- At the basement fridge, runs are ~28 min, with defrost recovery runs of
  ~45 min. The longest normal run is ≤ 46 min, so the notice comes at
  ~1.5 h. Its own learned limits must not be stretched by earlier unexplained
  long runs: these count only after they've been answered "new normal" or
  "one-off".

**N2 Dead device** (`cycling`). No start for `max(1.5 × learned maximum
pause, 30 min)`, not counting unavailable time.
- The LG Freezer (maximum pause 22 min) gets about 33 min, so 30 min applies.
- The fridges get about 1.5–2.5 h. These limits are approximate: the
  basement fridge's 102-min pause is real, while the 128/174/178-min gaps
  were missing data (R10).
- An **alarm** when the device is marked critical, otherwise a notice.
- **Not critical: only time without a sign of life counts.** A sign of life is
  any reading 5 W or more above the learned idle level. Demand-driven appliances
  pause for as long as they aren't needed: on 2026-11-09 the dehumidifier ran no
  compressor for 9 h because the air stayed dry (samples 51–56 % against a 55 %
  target), but ran its fan for about a minute every 63 min to sample the air
  (13–14 W). The old rule raised a false notice after 129 min. A dead unit stays
  flat at idle and still gets the notice. Fridges and freezers are critical, so
  their rule is unchanged: they must keep starting whatever the weather.
- Answered with "off on purpose" (catches R8).

**N4 Elevated draw between runs** (`cycling`). Between runs, the device draws
at least `max(learned pause-level p90, idle level) + 10 W` for 15 min or more.
- A notice, with a cooldown of 6 h. Suggested causes: something else on the
  same plug, a door ajar, a fan stuck on.
- **First, look for a shared load.** Before raising the notice, the guard
  checks whether some entity turned on within ±5 s of the step up. If so, the
  notice offers to link it ("Gillestugan Kitchen turned on at the same moment.
  Is it on this plug?"), instead of guessing at a fault.
- At the basement fridge the pause level is ~0–5 W. The kitchenette lights on
  the same extension cord add 22 W, both between runs and during them (R4).
- The more-powerful runs alone are not used as a signal: defrost recovery runs
  are also higher.

**N3 Short-cycling** (`cycling`). More starts per hour than `2 ×` the learned
p99 for 2 hours. A notice.

### State checks

**S1 Blind.** The plug has been unavailable longer than its limit (critical
devices: 30 min; others: 6 h), outside the restart grace. A notice: "can't see
the basement fridge".

**S2 Always-on out of band** (`always_on`). Below the learned band minus a
margin for 10 min or more, or above its maximum plus a margin. A notice.

### Household checks (across all plugs)

**H1 Common-mode drop.** Two or more watched plugs, on different integrations
or in different areas, go to 0 W or unavailable within 2 min. The guard
sends **one** notice, "several devices lost power or connection", instead of
one per plug. Per-plug N2 and S1 findings for those plugs are held for 30 min.
Catches R11.

**H2 Mains blip.** Entities from three or more unrelated integrations become
unavailable within 60 s, without a `homeassistant_stop` event. This is
recorded as a blip event, not as a notice. For the next 60 min, compressor
devices get closer attention: one failed start is enough for a notice. Seen
on 2026-11-02 at 07:35 and 15:02. Single-integration dropouts (Sungrow,
Zigbee or UniFi failing on its own) do not count.

## 7. Responses

A finding can be answered with one of the responses below. Each response
changes the model, and most lapse by themselves.

| Response | Offered on | What the guard does | Lapses |
|---|---|---|---|
| **Off on purpose** | N2, S1, H1, A2, A5 | Mode `off_on_purpose`: the dead-device and blind checks stop. Power, rating, relay and stall checks keep running | When the device runs normally again (5 starts for `cycling`, 10 min in band for `always_on`) |
| **Another load shares this plug** | N4, A4, S2 | Links the suggested entity (or one picked by the user) as a shared load and learns its draw | Permanent |
| **Something new is plugged in** | A4, N1, N3, S2 | Discards the learned model and goes to `learning`. Asks for the profile again | When learning completes |
| **New normal** | A4, N1, S2 | Keeps the model but adds the new level as normal: the envelope is widened and a 3-day period gets extra weight | Permanent |
| **One-off, it's fine** | All notices | Closes the finding. The event is not learned, and the same check stays quiet for that episode | When the episode ends |
| **I'll check it** | All | Acknowledged. The guard keeps watching and escalates if it continues | When the condition clears |
| **Snooze** 1 h / 24 h | All | No new notices for this device. Safety alarms (A1–A3, A5) still come, also in `paused` mode | After the time is up |
| **Cut power** | A1, A3, A4 alarms | Turns the relay off. The guard marks the device as cut and does not raise N2 for it | When the user switches it back on |
| **Turn back on** | A2, A5, N2 with relay off | Turns the relay on, then watches the next start closely | — |

Rules:
- An **unanswered alarm** repeats every 15 min, as a critical notification on
  the phone if configured, until it is answered or the condition clears.
  Switching the device back on is **not** an answer (R2).
- An **unanswered notice** is never treated as consent. It stays open in
  Repairs, and nothing is learned from the episode.
- Every finding and answer is kept in a log, persisted with the model: the
  last 200 per device. If the same check on the same device is answered
  "one-off" three times within 30 days, the next notice offers "make this
  check less sensitive?".

## 8. Notification channels

1. **Repairs issue**, the main channel. One issue per open finding, with
   **Fix** opening a menu flow that holds all the responses in section 7. It
   stays under Settings → Repairs until resolved, and works without a phone.
2. **Phone notification** through `notify.mobile_app_*` targets chosen in
   the options:
   - at most 3 buttons, chosen per check. A3 shows *Cut power / I'll check /
     More…*; N2 shows *Off on purpose / I'll check / More…*;
   - "More…" opens the Repairs issue;
   - responses come back as `mobile_app_notification_action` events, with
     action ids `appliance_guard:<entry>:<finding_id>:<response>`;
   - alarms use the `critical` / `alarm_stream` options when enabled.
3. **Persistent notification**, as a fallback when no phone is configured.
4. **Event entity** `event.<device>_finding`, so you can build your own
   automations (TTS, lights).
5. **Logbook** entries for every finding and answer.

## 9. Entities

One HA device per watched appliance, plus one household device.

| Entity | Type | Purpose |
|---|---|---|
| Mode | `select` | `watching` / `learning` / `off_on_purpose` / `paused` / `disabled`. Lets you declare something in advance ("the basement fridge goes off tomorrow") |
| Status | `sensor` (enum) | `ok`, `learning`, `notice`, `alarm`, `blind`, `off_on_purpose`, `paused`. Attributes: `finding`, `summary`, `since` |
| Problem | `binary_sensor` (device class `problem`) | On while an alarm is open |
| Finding | `event` | Fires on each finding and each answer |
| Relearn | `button` | Same as answering "something new" |
| Expected running power, Longest normal run, Longest normal pause | `sensor` (diagnostic) | Shows what was learned, so the limits can be judged |
| After a dropout | `sensor` (enum, diagnostic) | A5: what the relay did after losing contact. Only with a relay |
| Household: Last mains blip | `sensor` (timestamp) | H2 |

## 10. Configuration (UI)

**As built (0.1).** One config entry per watched plug, no household entry yet.

**Adding plugs.** *Add integration → Appliance Guard* finds every power
sensor on a device that also has a relay switch (skipping child lock, LED and
outage-memory switches) and that isn't watched yet. It reads 10 days of
history for each and offers:
- **Choose from the plugs found:** one list, one line per plug, such as
  "Basement fridge: cycles like a compressor: ~90 W, 17 starts a day; peak
  1615 W". Pre-ticked, matching the narrowed scope:
  - plugs that cycle like a compressor (at least 4 starts a day, never off
    longer than 4 h). Desks, heaters and media centres also have two levels, but
    rest for many hours and are not ticked;
  - names that sound like a fridge or freezer;
  - plugs whose peak comes within 80% of their rating, or above 1800 W when the
    rating is unknown ("rating unknown" is then shown).
  Phones to notify are picked once for all of them. Every ticked plug becomes
  its own entry with guessed settings (name, relay, rating, profile, critical);
  the rest are created through `integration_discovery` flows that create their
  entry directly. On the reference install this ticks the two fridges, the
  freezer, the dehumidifier, the kitchen island, the dishwasher and the laundry
  machine, and nothing else.
- **Pick a power sensor by hand:** any power sensor, then the full details
  form with the same guesses filled in.

**Changing settings.** *Configure* on an entry opens the same details form
(name, type, rating, critical, relay, phones). Saving reloads the entry, which
relearns from history and closes open findings. The power sensor can't be
changed; remove the entry and add it again. Mode and Relearn are entities on
the plug's guard device.

**Planned.**
- A household entry for shared options (phones, critical-alert opt-in,
  household checks), so phones aren't set per plug.
- Plugs added to HA later show up as discovered cards.
- Uncertain guesses (unknown rating, a name and a history that disagree) get a
  confirmation card instead of being accepted silently.
- A preview of what was learned before watching starts.
- Advanced limits per check.
- A one-time reminder for critical plugs whose *After a dropout* is still
  `not_seen`.

## 11. Architecture and testing

The package layout follows Fuse Guard: the logic is pure Python with no HA
imports, so it can be tested on its own, and a thin HA runtime sits around it.

```
custom_components/appliance_guard/
  model.py       learned features, seasonal envelope, forgetting
  detector.py    pure checks: reading stream in → findings out
  episodes.py    start/run/pause segmentation, unavailable handling
  responses.py   response handling and lapse rules
  household.py   H1/H2 across devices
  watcher.py     HA runtime: state listeners, context matching, timers, Store
  learn.py       recorder queries (long-term statistics + raw history) via executor
  notify.py      repairs issues + fix flow, mobile actions, persistent fallback
  config_flow.py config, subentries, preview
  sensor.py binary_sensor.py select.py button.py event.py diagnostics.py
tests/
  fixtures/      real recorder history from one home, without entity ids or names
  test_replay.py R1–R12
```

**Replay fixtures** come from the reference installation's recorder:
- R3, R4, R9, R10 and R12 are raw readings from the last 10 days. They need
  exporting soon, because the recorder keeps raw readings for only 10 days.
- R1, R2, R5, R6, R7 and R8 exist only as hourly long-term statistics. For
  these, the test generates readings that match the recorded hourly mean, min
  and max, using the burst pattern deduced in R1. That is a synthetic stand-in,
  and the test says so.

Every replay asserts:
- the finding raised;
- its severity;
- the time from the start of the event to the finding;
- that the normal stretches before and after raise nothing.

## 12. Reference installation: expected setup

| Plug | Profile | Critical | Notes |
|---|---|---|---|
| LG Fridge (TS011F) | cycling | yes | Idle reads exactly 0.0 or 3.0 W, so 0 W is not a "dead" signal. Seasonal peak up to ~124 W |
| Basement Fridge (TS011F) | cycling | yes | Start surge up to 1615 W. Runs ~28 min, pauses ~55–62 min, defrost about every 31–37 h. Shared load: kitchenette lights (+22 W) on an extension cord |
| LG Freezer (Aqara) | cycling | yes | Runs ~2 h, pauses 21–22 min, very regular. The easiest dead-device case |
| Dehumidifier (Aqara) | cycling | no | New unit: 800–987 W surge, then ~268 W |
| Servers + printer (Aqara) | always_on | no | 48–58 W band. The printer's ~1 kW warm-ups are learned as normal (R9). The printer's page counter could be linked as a shared load |
| Network, Fiberconverter | always_on | yes | Very steady bands |
| Kitchen Island (Shelly Plug S) | on_demand | — | A1 is the point (R5) |
| Dishwasher, Laundry Machine, Guest Kitchen | on_demand | — | ~2000 W, close to the plugs' ratings |
| Bedroom Radiator | on_demand | — | ~770 W on low |
| Two desks, workshop lights & vent | on_demand, irregular | — | Rating and relay checks only |
| 3D printer, filament dryers | on_demand | — | Heater cycling with 600–700 W bursts, which are normal |

## 13. Relation to Fuse Guard

The two stay **separate integrations**: Fuse Guard protects the main fuse, the
guard protects appliances and plugs. Two points where they touch:

- Fuse Guard v2 plans to **switch off** the dehumidifier, dishwasher and
  washing machine. Those relay changes are HA service calls, so A2 ignores
  them. While Fuse Guard has a load shed, the guard treats the device as
  `off_on_purpose`.
- R3 shows that restarting a compressor 85 s after it stopped can stall it.
  Fuse Guard v2's **minimum off time** for compressor loads should therefore be
  at least 5 min. Its proposed 10 min is fine.

## 14. Open questions

1. **Name.** "Appliance Guard" still needs checking against HACS, core and
   brands. Alternatives: "Plug Sentinel", "Appliance Guard" (which pairs with
   Fuse Guard).
2. Should **Cut power** be automatic for A1 or A3 after N unanswered repeats?
   The default proposal is never automatic: switching a fridge off unasked is
   its own hazard. It could be an opt-in per device.
3. Should H2 use a stronger signal than integrations dropping out? The UPS
   might expose an "on battery" state, and the Sungrow inverters a grid-state
   register.
4. Plug ratings for the TS011F variants and the Shelly Power Strip 4 Gen4
   need checking against the actual hardware.
5. Should a fridge thermometer, when there is one, feed N1 and N2 directly?
   It is planned for later (section 15), but it changes how critical devices
   are configured.

## 15. Later versions

- **Temperature sensors:** an optional fridge/freezer thermometer per device.
  The cabinet warming is a direct signal, and R4 would have been explained at
  once.
- **Energy drift:** daily energy per device against the seasonal envelope,
  for slow failures (a worn door seal, dust on the condenser).
- **Cycle-shape learning for `on_demand` devices** (washing machine
  programmes), so a stuck heater in a dishwasher cycle can be caught.
- **Weekly digest** (opt-in): what was learned, what was answered, and the
  checks that keep needing "one-off" answers.
