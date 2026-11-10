# Reference fixtures

Real recorder history from one home, used by the replay tests. Entity ids,
names and rooms have been removed; aliases describe the appliance only.

- **Raw history** covers 2026-10-28 to 2026-11-07 (plus one later night), every recorded state row
  including `unavailable`.
- **Timestamps** are Unix epoch seconds (UTC). Tests use the zone in
  `manifest.json` for local times.

| Path | Columns | Notes |
|---|---|---|
| `raw/<alias>.csv.gz` | `ts,state,ctx_user,ctx_parent` | `ctx_user` / `ctx_parent` = 1 when the change came from a Home Assistant service call by a user or an automation |
| `household/ha_lifecycle.csv` | `ts,event` | `homeassistant_start` / `homeassistant_stop` |
| `manifest.json` | | Alias → integration platform, row count, first and last timestamp |

## What the tests use them for

| Alias | Shows |
|---|---|
| `bsmnt_fridge` | Normal compressor starts with one-reading inrush spikes up to ~1.6 kW; a failed start after a mains blip on 2026-11-02 15:02 (~850 W for 35 s, then a normal restart); a ~22 W light on the same plug some evenings (`kitchenette_light`) |
| `lg_fridge`, `lg_freezer`, `dehumidifier` | Compressor cycling, idle readings of exactly 0 W, Zigbee plugs unavailable for up to 2 h after a restart |
| `network` | An always-on load |
| `dehumidifier_dry_night` | 2026-11-09 14:00 to 11-10 07:00: compressor runs every ~64 min until 21:03, then a dry night with only ~1-minute fan samples (13-14 W) every ~63 min. Raised a false "not starting" notice before non-critical appliances needed no sign of life |
| `servers_printer` | A laser printer's ~1 kW fuser warm-up on a ~50 W plug |
| `*_switch` | Relay state through dropouts and restarts: none switched itself off, and every gap came back on |
