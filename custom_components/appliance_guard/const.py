"""Constants for Appliance Guard."""

DOMAIN = "appliance_guard"

CONF_POWER_ENTITY = "power_entity"
CONF_RELAY_ENTITY = "relay_entity"
CONF_NAME = "name"
CONF_PROFILE = "profile"
CONF_RATING_W = "rating_w"
CONF_CRITICAL = "critical"
CONF_NOTIFY = "notify_targets"
CONF_KEEP_ON = "keep_on"

HISTORY_DAYS = 10
RELEARN_INTERVAL_H = 24
TICK_S = 60
REPLAY_HOURS = 12  # history replayed silently at start, so open runs/pauses are known
COMMAND_WINDOW_S = 10  # a relay change this soon after a matching service call was commanded
ALARM_REPEAT_S = 15 * 60
ALARM_REPEATS = 4
OFF_ON_PURPOSE_RUNS = 5  # normal runs that end "off on purpose" for a cycling device
# Keep powered: turn the relay back on by itself, at most this often, and never
# right after it was over its rating (that is the plug protecting itself).
KEEP_ON_TRIES = 3
KEEP_ON_WINDOW_S = 3600
KEEP_ON_AFTER_A1_S = 600
KEEP_ON_VERIFY_S = 60

EVENT_FINDING = f"{DOMAIN}_finding"

# Modes (select entity). "learning" is a status, not a mode.
MODE_WATCHING = "watching"
MODE_OFF_ON_PURPOSE = "off_on_purpose"
MODE_PAUSED = "paused"
MODE_DISABLED = "disabled"
MODES = [MODE_WATCHING, MODE_OFF_ON_PURPOSE, MODE_PAUSED, MODE_DISABLED]

# Responses (DESIGN.md section 7). The fix flow and phone buttons use these ids.
R_OFF_ON_PURPOSE = "off_on_purpose"
R_SOMETHING_NEW = "something_new"
R_NEW_NORMAL = "new_normal"
R_ONE_OFF = "one_off"
R_WILL_CHECK = "will_check"
R_SNOOZE_1H = "snooze_1h"
R_SNOOZE_24H = "snooze_24h"
R_CUT_POWER = "cut_power"
R_TURN_BACK_ON = "turn_back_on"
RESPONSES = [R_OFF_ON_PURPOSE, R_SOMETHING_NEW, R_NEW_NORMAL, R_ONE_OFF, R_WILL_CHECK,
             R_SNOOZE_1H, R_SNOOZE_24H, R_CUT_POWER, R_TURN_BACK_ON]
NEEDS_RELAY = {R_CUT_POWER, R_TURN_BACK_ON}

# Phone action ids: "<prefix>|<entry_id>|<finding_id>|<response>"
ACTION_PREFIX = "AWATCH"

# Plug ratings guessed from the device model, confirmed by the user at setup.
RATING_BY_MODEL = {
    "Shelly Plug S": 2500,
    "lumi.plug.maeu01": 2300,
    "TS011F": 3680,
    # Shelly Power Strip 4 Gen4: 12 A per socket (16 A / 3680 W for the whole strip,
    # which needs a shared limit across its sockets: not built yet).
    "S4PL-00416EU": 2760,
}

CRITICAL_WORDS = ("fridge", "freezer", "kyl", "frys")
