# Changelog

## Unreleased

- Setup only calls a plug cycling when it has the 20 complete runs needed to
  learn it, not 10.

## 2026.11.5

- Fridges and freezers that run most of the time over a wide power range (an LG
  freezer at 33-90 W) are learned again: idle and running are now told apart on a
  log scale.

## 2026.11.4

- Open alerts, what was already reported, snooze and Keep powered's state
  survive restarts and reloads. A problem still going on that nobody was told
  about is raised again; one already raised or answered is not.
- What it learned is kept instead of relearned every day, and leaves out time
  the plug was switched off and pauses far longer than the rest.
- "A different appliance is plugged in" learns the new appliance from that
  moment. **Relearn** learns again from the last 10 days.
- Checks: "not starting" for critical cycling appliances only. "Running far
  longer than normal" and "drawing more than usual between runs" removed.
- New setting **Only Home Assistant switches this plug** (on by default for
  critical plugs). "Switched itself off" and Keep powered need it.
- Keep powered needs the plug rating, doesn't act after a draw close to the
  rating, and after giving up waits until the alert is answered.
- No rating guess for TS011F plugs (they come as 10 A and 16 A).
- Learned sensors only where a check uses them.
- The status sensor's `checks` attribute shows, per check, whether it is watching or why not.
- "Off on purpose" ends when a non-cycling appliance is switched back on, and for an
  always-on load when it draws half its learned minimum again (small loads included).

## 2026.11.3

- Always-on appliances: stopped drawing, switched off from Home Assistant,
  Keep powered.

## 2026.11.2

- The setup scan no longer hangs for hours on plugs with long histories.

## 2026.11.1

- "Not starting" for non-critical appliances needs no sign of life, not just a
  long pause.

## 2026.11.0

- First release.
