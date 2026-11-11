"""The watcher inside Home Assistant: states in, repairs issues and answers out."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.core import Context, CoreState, HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

from custom_components.appliance_guard.const import (
    CONF_CRITICAL,
    CONF_HA_ONLY,
    CONF_KEEP_ON,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_POWER_ENTITY,
    CONF_PROFILE,
    CONF_RATING_W,
    CONF_RELAY_ENTITY,
    DOMAIN,
    EVENT_FINDING,
    MODE_OFF_ON_PURPOSE,
)

POWER = "sensor.kettle_power"
RELAY = "switch.kettle"
OPTIONS = {
    CONF_NAME: "Kettle",
    CONF_PROFILE: "on_demand",
    CONF_RATING_W: 2500,
    CONF_CRITICAL: False,
    CONF_HA_ONLY: True,
    CONF_RELAY_ENTITY: RELAY,
    CONF_NOTIFY: ["mobile_app_phone"],
}


@pytest.fixture(autouse=True)
def auto_enable(recorder_mock, enable_custom_integrations):
    """Recorder first, then hass with custom integrations."""
    yield


@pytest.fixture
async def setup(hass: HomeAssistant):
    hass.states.async_set(POWER, "1", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "on")
    notify = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options=OPTIONS, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry, notify


def _issues(hass: HomeAssistant) -> list[ir.IssueEntry]:
    return [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN]


async def test_over_rating_raises_alarm_issue_event_and_phone(hass: HomeAssistant, setup):
    entry, notify = setup
    events = []
    hass.bus.async_listen(EVENT_FINDING, callback(lambda e: events.append(e.data)))
    assert hass.states.get("sensor.kettle_guard_status").state == "ok"

    hass.states.async_set(POWER, "3302", {"device_class": "power", "unit_of_measurement": "W"})
    await hass.async_block_till_done()

    issues = _issues(hass)
    assert len(issues) == 1 and issues[0].severity == ir.IssueSeverity.ERROR
    assert issues[0].translation_placeholders["check"] == "over the plug's rating"
    assert events and events[0]["check"] == "A1"
    assert hass.states.get("binary_sensor.kettle_guard_problem").state == "on"
    assert hass.states.get("sensor.kettle_guard_status").state == "alarm"
    assert hass.states.get("event.kettle_guard_finding").attributes["event_type"] == "alarm"
    assert len(notify) == 1
    actions = notify[0].data["data"]["actions"]
    assert actions[0]["title"] == "Cut power" and actions[-1]["uri"] == "/config/repairs"


async def test_relay_off_by_itself_alarms_but_commanded_off_does_not(
        hass: HomeAssistant, setup):
    async_mock_service(hass, "switch", "turn_off")
    calls: list[Context] = []
    hass.bus.async_listen("call_service", callback(lambda e: calls.append(e.context)))

    # Switched from HA: the state change carries the service call's context.
    await hass.services.async_call("switch", "turn_off", {"entity_id": RELAY}, blocking=True)
    hass.states.async_set(RELAY, "off", context=calls[-1])
    await hass.async_block_till_done()
    assert _issues(hass) == []

    hass.states.async_set(RELAY, "on", context=Context())
    hass.states.async_set(RELAY, "off", context=Context())  # overload protection tripped
    await hass.async_block_till_done()
    issues = _issues(hass)
    assert len(issues) == 1
    assert issues[0].translation_placeholders["check"] == "plug switched itself off"


async def test_fix_flow_cut_power(hass: HomeAssistant, setup, hass_client):
    entry, _ = setup
    turn_off = async_mock_service(hass, "switch", "turn_off")
    hass.states.async_set(POWER, "3302")
    await hass.async_block_till_done()
    issue = _issues(hass)[0]

    assert await async_setup_component(hass, "repairs", {})
    client = await hass_client()
    resp = await client.post("/api/repairs/issues/fix",
                             json={"handler": DOMAIN, "issue_id": issue.issue_id})
    flow: dict[str, Any] = await resp.json()
    assert flow["type"] == FlowResultType.MENU
    assert "cut_power" in flow["menu_options"] and "snooze_1h" in flow["menu_options"]

    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}",
                             json={"next_step_id": "cut_power"})
    assert (await resp.json())["type"] == FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert len(turn_off) == 1 and turn_off[0].data["entity_id"] == RELAY
    assert _issues(hass) == []
    assert entry.runtime_data.mode == MODE_OFF_ON_PURPOSE
    assert hass.states.get("select.kettle_guard_mode").state == MODE_OFF_ON_PURPOSE


async def test_phone_action_snoozes_and_clears_the_issue(hass: HomeAssistant, setup):
    entry, notify = setup
    hass.states.async_set(POWER, "3302")
    await hass.async_block_till_done()
    action = next(a["action"] for a in notify[0].data["data"]["actions"]
                  if a["action"].endswith("|will_check"))
    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
    await hass.async_block_till_done()
    assert _issues(hass) == []
    assert hass.states.get("binary_sensor.kettle_guard_problem").state == "off"
    assert hass.states.get("event.kettle_guard_finding").attributes["event_type"] == "response"


async def test_paused_still_lets_safety_alarms_through(hass: HomeAssistant, setup):
    entry, _ = setup
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": "select.kettle_guard_mode",
                                    "option": "paused"}, blocking=True)
    hass.states.async_set(POWER, "3302")
    await hass.async_block_till_done()
    assert len(_issues(hass)) == 1


async def test_unload_removes_issues(hass: HomeAssistant, setup):
    entry, _ = setup
    hass.states.async_set(POWER, "3302")
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert _issues(hass) == []


async def test_cycling_device_learns_from_history(hass: HomeAssistant, monkeypatch):
    from replay import raw

    from custom_components.appliance_guard import watcher as watcher_module

    readings = raw("bsmnt_fridge")

    async def fake_load(_hass, _entity, _days):
        return readings

    monkeypatch.setattr(watcher_module, "async_load_readings", fake_load)
    hass.states.async_set(POWER, "0", {"device_class": "power", "unit_of_measurement": "W"})
    entry = MockConfigEntry(domain=DOMAIN, title="Fridge", data={CONF_POWER_ENTITY: POWER},
                            options={CONF_NAME: "Fridge", CONF_PROFILE: "cycling",
                                     CONF_CRITICAL: True}, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)

    assert hass.states.get("sensor.fridge_guard_status").state == "ok"
    assert 85 <= float(hass.states.get("sensor.fridge_guard_normal_running_power").state) <= 95
    assert int(hass.states.get("sensor.fridge_guard_longest_normal_pause").state) <= 110
    # The replay is silent: nothing from the past 12 hours became an issue.
    assert _issues(hass) == []


async def test_config_flow_guesses_from_the_device(hass: HomeAssistant):
    hass.states.async_set("sensor.power_to_basement_fridge_power", "90",
                          {"device_class": "power", "unit_of_measurement": "W",
                           "friendly_name": "Power to Basement Fridge Power"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_POWER_ENTITY: "sensor.power_to_basement_fridge_power"})
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "details"
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema[CONF_NAME].default() == "Basement Fridge Power"
    assert schema[CONF_CRITICAL].default() is True

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {
        CONF_NAME: "Basement fridge", CONF_PROFILE: "cycling", CONF_CRITICAL: True})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Basement fridge"
    assert result["data"] == {CONF_POWER_ENTITY: "sensor.power_to_basement_fridge_power"}

    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {CONF_POWER_ENTITY: "sensor.power_to_basement_fridge_power"})
    assert again["type"] == FlowResultType.ABORT


# ---- A5: came back off after a dropout -----------------------------------------

async def _tick_later(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_plug_back_off_after_a_dropout_alarms_and_is_recorded(
        hass: HomeAssistant, setup, hass_storage, freezer):
    entry, notify = setup
    after = "sensor.kettle_guard_after_a_dropout"
    assert hass.states.get(after).state == "not_seen"

    hass.states.async_set(RELAY, "unavailable")
    hass.states.async_set(RELAY, "on")  # came back on: recorded, no finding
    await _tick_later(hass, freezer, 360)
    assert _issues(hass) == []
    assert hass.states.get(after).state == "came_back_on"

    hass.states.async_set(RELAY, "unavailable")
    hass.states.async_set(RELAY, "off")
    await hass.async_block_till_done()
    assert _issues(hass) == []  # grace time: it may still switch itself on
    await _tick_later(hass, freezer, 360)
    issues = _issues(hass)
    assert len(issues) == 1
    assert issues[0].translation_placeholders["check"] == "plug came back off after a dropout"
    state = hass.states.get(after)
    assert state.state == "came_back_off"
    assert state.attributes["times_back_on"] == 1 and state.attributes["times_back_off"] == 1
    await _tick_later(hass, freezer, 10)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]["dropouts"]
    assert stored["back_off"] == 1 and stored["last"]["outcome"] == "off"


async def test_plug_back_off_after_ha_restart_alarms(hass: HomeAssistant, monkeypatch, freezer):
    """HA starts, and the relay that was on before the stop is off now."""
    from custom_components.appliance_guard import watcher as watcher_module

    now = dt_util.utcnow().timestamp()

    async def fake_relay(_hass, _entity, _days):
        return [(now - 7200, "on"), (now - 5, "off")], [now - 3600]

    monkeypatch.setattr(watcher_module, "async_load_relay", fake_relay)
    hass.set_state(CoreState.not_running)
    hass.states.async_set(POWER, "0", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "off")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options=OPTIONS, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.set_state(CoreState.running)
    await _tick_later(hass, freezer, 360)
    issues = _issues(hass)
    assert len(issues) == 1 and "while Home Assistant was down" in (
        issues[0].translation_placeholders["summary"])


async def test_relay_off_from_ha_before_a_restart_stays_quiet(
        hass: HomeAssistant, monkeypatch, freezer):
    from custom_components.appliance_guard import watcher as watcher_module

    now = dt_util.utcnow().timestamp()

    async def fake_relay(_hass, _entity, _days):
        return [(now - 7200, "on"), (now - 4000, "off"), (now - 5, "off")], [now - 3600]

    monkeypatch.setattr(watcher_module, "async_load_relay", fake_relay)
    hass.set_state(CoreState.not_running)
    hass.states.async_set(POWER, "0", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "off")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options=OPTIONS, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    hass.set_state(CoreState.running)
    await _tick_later(hass, freezer, 360)
    assert _issues(hass) == []


# ---- setup: pick from the plugs found ------------------------------------------

def _plug(hass: HomeAssistant, owner: MockConfigEntry, name: str, model: str) -> str:
    dev = dr.async_get(hass).async_get_or_create(
        config_entry_id=owner.entry_id, identifiers={("test", name)}, name=name, model=model)
    ent_reg = er.async_get(hass)
    slug = name.lower().replace(" ", "_")
    power = ent_reg.async_get_or_create(
        "sensor", "test", f"{slug}_power", device_id=dev.id,
        original_device_class="power", suggested_object_id=f"{slug}_power").entity_id
    relay = ent_reg.async_get_or_create(
        "switch", "test", slug, device_id=dev.id, suggested_object_id=slug).entity_id
    ent_reg.async_get_or_create("switch", "test", f"{slug}_child_lock", device_id=dev.id,
                                suggested_object_id=f"{slug}_child_lock")
    hass.states.async_set(power, "0", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(relay, "on")
    return power


async def test_setup_offers_found_plugs_and_ticks_the_ones_worth_watching(
        hass: HomeAssistant, monkeypatch):
    from replay import raw

    from custom_components.appliance_guard import config_flow, watcher

    owner = MockConfigEntry(domain="test")
    owner.add_to_hass(hass)
    fridge = _plug(hass, owner, "Basement fridge", "TS011F")
    lamp = _plug(hass, owner, "Desk lamp", "TS011F")
    start = raw("bsmnt_fridge")[0][0]
    evenings = [(start + d * 86400 + h * 3600, w)  # a lamp: on 18-23 each evening
                for d in range(10) for h, w in ((18, 40.0), (23, 0.0))]
    history = {fridge: raw("bsmnt_fridge"), lamp: evenings}

    async def fake_load(_hass, entity, _days):
        return history.get(entity, [])

    monkeypatch.setattr(config_flow, "async_load_readings", fake_load)
    monkeypatch.setattr(watcher, "async_load_readings", fake_load)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "plugs"})
    assert result["type"] == FlowResultType.SHOW_PROGRESS  # reading history
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] == FlowResultType.FORM and result["step_id"] == "pick"
    schema = {str(k): k for k in result["data_schema"].schema}
    assert schema["plugs"].default() == [fridge]  # the lamp is offered but not ticked

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"plugs": [fridge, lamp], CONF_NOTIFY: []})
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["description"] == "picked"
    assert result["description_placeholders"]["names"] == "Basement fridge, Desk lamp"
    await hass.async_block_till_done(wait_background_tasks=True)
    entries = {e.title: e for e in hass.config_entries.async_entries(DOMAIN)}
    assert set(entries) == {"Basement fridge", "Desk lamp"}
    f = entries["Basement fridge"].options
    assert f[CONF_PROFILE] == "cycling" and f[CONF_CRITICAL] is True
    # TS011F plugs come as 10 A and 16 A: no rating is guessed for them.
    assert f[CONF_RATING_W] is None and f[CONF_RELAY_ENTITY] == "switch.basement_fridge"
    assert f[CONF_HA_ONLY] is True  # critical plugs: switching off by itself is an alarm
    assert entries["Desk lamp"].options[CONF_HA_ONLY] is False
    assert entries["Desk lamp"].options[CONF_PROFILE] == "on_demand"

    # Only plugs not yet watched are offered next time.
    again = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert again["type"] == FlowResultType.FORM and again["step_id"] == "manual"


async def test_phones_are_offered_by_their_app_name(hass: HomeAssistant):
    from custom_components.appliance_guard.config_flow import phone_names

    MockConfigEntry(domain="mobile_app", title="Kitchen phone",
                    data={"device_name": "Pixel 7"}).add_to_hass(hass)
    for svc in ("mobile_app_pixel_7", "mobile_app_old_phone"):
        hass.services.async_register("notify", svc, lambda call: None)
    assert phone_names(hass) == {"mobile_app_pixel_7": "Kitchen phone",
                                 "mobile_app_old_phone": "old_phone"}


# ---- keep powered ------------------------------------------------------------------

@pytest.fixture
async def keep_on(hass: HomeAssistant):
    hass.states.async_set(POWER, "70", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "on")
    async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options={**OPTIONS, CONF_KEEP_ON: True}, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry


async def test_keep_powered_turns_a_tripped_plug_back_on(hass: HomeAssistant, keep_on, freezer):
    turn_on = async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set(RELAY, "off", context=Context())  # switched itself off
    await hass.async_block_till_done()
    issues = _issues(hass)
    assert len(issues) == 1 and issues[0].severity == ir.IssueSeverity.WARNING
    assert issues[0].translation_placeholders["summary"].endswith("Turning it back on.")
    assert turn_on == []  # waits a moment for an overload reading that raced the relay

    await _tick_later(hass, freezer, 11)
    assert len(turn_on) == 1 and turn_on[0].data["entity_id"] == RELAY
    hass.states.async_set(RELAY, "on", context=turn_on[0].context)
    await _tick_later(hass, freezer, 61)
    assert len(_issues(hass)) == 1  # no alarm on top


async def test_keep_powered_not_after_an_overload(hass: HomeAssistant, keep_on, freezer):
    turn_on = async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set(POWER, "3302")
    hass.states.async_set(RELAY, "off", context=Context())
    await hass.async_block_till_done()
    await _tick_later(hass, freezer, 11)
    assert turn_on == []
    summaries = [i.translation_placeholders["summary"] for i in _issues(hass)
                 if i.severity == ir.IssueSeverity.ERROR]
    assert any("Not turned back on: it was just over its rating" in s for s in summaries), [
        (i.severity, i.translation_placeholders["summary"]) for i in _issues(hass)]


async def test_keep_powered_gives_up_and_alarms(hass: HomeAssistant, keep_on, freezer):
    turn_on = async_mock_service(hass, "switch", "turn_on")  # the relay never follows
    hass.states.async_set(RELAY, "off", context=Context())
    await hass.async_block_till_done()
    await _tick_later(hass, freezer, 11)
    for _ in range(4):
        await _tick_later(hass, freezer, 61)
    assert len(turn_on) == 3
    alarms = [i for i in _issues(hass) if i.severity == ir.IssueSeverity.ERROR]
    assert len(alarms) == 1
    assert "did not come back on after 3 tries" in alarms[0].translation_placeholders["summary"]


async def test_keep_powered_respects_off_on_purpose(hass: HomeAssistant, keep_on, freezer):
    turn_on = async_mock_service(hass, "switch", "turn_on")
    keep_on.runtime_data.set_mode(MODE_OFF_ON_PURPOSE)
    hass.states.async_set(RELAY, "off", context=Context())
    await _tick_later(hass, freezer, 120)
    assert turn_on == []


async def test_always_on_learns_its_minimum_power(hass: HomeAssistant, monkeypatch):
    from replay import raw

    from custom_components.appliance_guard import watcher as watcher_module

    readings = raw("network")

    async def fake_load(_hass, _entity, _days):
        return readings

    monkeypatch.setattr(watcher_module, "async_load_readings", fake_load)
    hass.states.async_set(POWER, "72", {"device_class": "power", "unit_of_measurement": "W"})
    entry = MockConfigEntry(domain=DOMAIN, title="Network", data={CONF_POWER_ENTITY: POWER},
                            options={CONF_NAME: "Network", CONF_PROFILE: "always_on",
                                     CONF_CRITICAL: True}, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert 65 <= float(hass.states.get("sensor.network_guard_normal_minimum_power").state) <= 72
    assert hass.states.get("sensor.network_guard_status").state == "ok"
    assert _issues(hass) == []


# ---- across reloads and restarts -------------------------------------------------

async def _reload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_an_open_alarm_survives_a_reload_without_calling_the_phone_again(
        hass: HomeAssistant, setup):
    entry, notify = setup
    hass.states.async_set(POWER, "3302")
    await hass.async_block_till_done()
    before = [i.issue_id for i in _issues(hass)]
    assert len(before) == 1 and len(notify) == 1

    await _reload(hass, entry)
    assert [i.issue_id for i in _issues(hass)] == before
    assert len(notify) == 1
    assert hass.states.get("sensor.kettle_guard_status").state == "alarm"

    fid = next(iter(entry.runtime_data.findings))
    await entry.runtime_data.async_respond(fid, "will_check")
    await _reload(hass, entry)
    assert _issues(hass) == []


@pytest.fixture
async def dead_freezer(hass: HomeAssistant, monkeypatch):
    """A freezer that cycled normally, then stopped starting 5 h ago."""
    from test_resume import H, _history

    from custom_components.appliance_guard import watcher as watcher_module

    now = dt_util.utcnow().timestamp()
    readings = _history(now - 5 * H) + [(now - 4 * H, 2.0), (now - 3 * H, 1.0)]

    async def fake_load(_hass, _entity, _days):
        return readings

    monkeypatch.setattr(watcher_module, "async_load_readings", fake_load)
    hass.states.async_set(POWER, "1", {"device_class": "power", "unit_of_measurement": "W"})
    notify = async_mock_service(hass, "notify", "mobile_app_phone")
    entry = MockConfigEntry(domain=DOMAIN, title="Freezer", data={CONF_POWER_ENTITY: POWER},
                            options={CONF_NAME: "Freezer", CONF_PROFILE: "cycling",
                                     CONF_CRITICAL: True, CONF_NOTIFY: ["mobile_app_phone"]},
                            unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry, notify


async def test_a_dead_freezer_stays_alarmed_across_reloads(hass: HomeAssistant, dead_freezer):
    entry, notify = dead_freezer
    issues = _issues(hass)
    assert len(issues) == 1 and issues[0].translation_placeholders["check"] == "not starting"
    assert len(notify) == 1

    await _reload(hass, entry)
    await _reload(hass, entry)
    assert [i.issue_id for i in _issues(hass)] == [issues[0].issue_id]
    assert len(notify) == 1
    assert hass.states.get("sensor.freezer_guard_status").state == "alarm"


async def test_an_answered_alarm_is_not_raised_again_after_a_reload(
        hass: HomeAssistant, dead_freezer):
    entry, notify = dead_freezer
    fid = next(iter(entry.runtime_data.findings))
    await entry.runtime_data.async_respond(fid, "will_check")
    assert _issues(hass) == []

    await _reload(hass, entry)
    assert _issues(hass) == []
    assert len(notify) == 1


async def test_ha_stopping_saves_what_was_raised(
        hass: HomeAssistant, dead_freezer, hass_storage, freezer):
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    entry, _ = dead_freezer
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await _tick_later(hass, freezer, 5)
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}"]["data"]
    assert [f["check"] for f in stored["findings"].values()] == ["N2"]
    assert [(r["check"], r["severity"]) for r in stored["reported"]] == [("N2", "alarm")]


# ---- what was learned is kept --------------------------------------------------

def _pause_min(hass: HomeAssistant) -> float:
    return float(hass.states.get("sensor.freezer_guard_longest_normal_pause").state)


def _slow_freezer(now: float) -> list[tuple[float, float | None]]:
    """Runs 20 min, pauses 100 min."""
    out: list[tuple[float, float | None]] = []
    t = now - 80 * 7200.0
    while t < now - 3600:
        out += [(t, 40.0), (t + 1200, 1.0)]
        t += 7200
    return out


async def test_what_was_learned_is_kept_across_reloads(
        hass: HomeAssistant, dead_freezer, monkeypatch):
    from custom_components.appliance_guard import watcher as watcher_module

    entry, _ = dead_freezer
    learned = _pause_min(hass)
    assert 35 <= learned <= 45

    slow = _slow_freezer(dt_util.utcnow().timestamp())

    async def slow_load(_hass, _entity, _days):
        return slow

    monkeypatch.setattr(watcher_module, "async_load_readings", slow_load)
    await _reload(hass, entry)
    assert _pause_min(hass) == learned  # history changed, what was learned did not

    await hass.services.async_call("button", "press",
                                   {"entity_id": "button.freezer_guard_relearn"}, blocking=True)
    await hass.async_block_till_done()
    assert 95 <= _pause_min(hass) <= 105




async def test_a_different_appliance_is_learned_from_now(hass: HomeAssistant, dead_freezer):
    entry, _ = dead_freezer
    fid = next(iter(entry.runtime_data.findings))
    assert "something_new" in entry.runtime_data.offers(fid)
    await entry.runtime_data.async_respond(fid, "something_new")
    assert hass.states.get("sensor.freezer_guard_status").state == "learning"
    await _reload(hass, entry)  # the old history still doesn't count
    assert hass.states.get("sensor.freezer_guard_status").state == "learning"
    assert entry.runtime_data.model is None


# ---- only what applies ---------------------------------------------------------

async def test_only_sensors_that_apply_and_old_ones_are_removed(hass: HomeAssistant):
    hass.states.async_set(POWER, "1", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "on")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options=OPTIONS, unique_id=POWER)
    entry.add_to_hass(hass)
    ent_reg = er.async_get(hass)
    for key in ("longest_run", "longest_pause"):  # left by an earlier version
        ent_reg.async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_{key}",
                                    config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    keys = {e.unique_id.removeprefix(f"{entry.entry_id}_")
            for e in er.async_entries_for_config_entry(ent_reg, entry.entry_id)
            if e.domain == "sensor"}
    assert keys == {"status", "after_dropout"}  # an on-demand plug learns nothing


async def test_keep_powered_needs_ha_to_be_the_only_switch(hass: HomeAssistant):
    hass.states.async_set(POWER, "70", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "on")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options={**OPTIONS, CONF_KEEP_ON: True, CONF_HA_ONLY: False},
                            unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    turn_on = async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set(RELAY, "off", context=Context())  # its button
    await hass.async_block_till_done()
    assert entry.runtime_data.keep_on is False
    assert _issues(hass) == [] and turn_on == []


# ---- Keep powered and "switched itself off" are safe -------------------------------

async def test_keep_powered_needs_a_rating(hass: HomeAssistant):
    hass.states.async_set(POWER, "70", {"device_class": "power", "unit_of_measurement": "W"})
    hass.states.async_set(RELAY, "on")
    entry = MockConfigEntry(domain=DOMAIN, title="Kettle", data={CONF_POWER_ENTITY: POWER},
                            options={**OPTIONS, CONF_KEEP_ON: True, CONF_RATING_W: None},
                            unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.keep_on is False
    status = hass.states.get("sensor.kettle_guard_status")
    assert status.attributes["keep_powered"] == "off"


async def test_keep_powered_not_after_drawing_close_to_the_rating(
        hass: HomeAssistant, keep_on, freezer):
    """The plug tripped before a reading over the rating arrived."""
    turn_on = async_mock_service(hass, "switch", "turn_on")
    hass.states.async_set(POWER, "2300")  # 92 % of 2500 W: no A1 yet
    await hass.async_block_till_done()
    hass.states.async_set(RELAY, "off", context=Context())
    hass.states.async_set(POWER, "0")
    await hass.async_block_till_done()
    await _tick_later(hass, freezer, 11)
    assert turn_on == []
    summaries = [i.translation_placeholders["summary"] for i in _issues(hass)]
    assert any("drew 2300 W just before" in s for s in summaries), summaries


async def _give_up(hass: HomeAssistant, freezer) -> list:
    turn_on = async_mock_service(hass, "switch", "turn_on")  # the relay never follows
    hass.states.async_set(RELAY, "off", context=Context())
    await hass.async_block_till_done()
    await _tick_later(hass, freezer, 11)
    for _ in range(4):
        await _tick_later(hass, freezer, 61)
    assert len(turn_on) == 3
    return turn_on


async def test_keep_powered_waits_for_an_answer_after_giving_up(
        hass: HomeAssistant, keep_on, freezer):
    turn_on = await _give_up(hass, freezer)
    assert hass.states.get("sensor.kettle_guard_status").attributes[
        "keep_powered"] == "waiting_for_answer"

    # Two hours later someone turns it on at the plug, and it trips again.
    await _tick_later(hass, freezer, 7200)
    hass.states.async_set(RELAY, "on", context=Context())
    hass.states.async_set(RELAY, "off", context=Context())
    await _tick_later(hass, freezer, 120)
    assert len(turn_on) == 3  # the old sliding window would have tried again
    assert any("waits until the earlier alert is answered" in
               i.translation_placeholders["summary"] for i in _issues(hass))

    await _reload(hass, keep_on)  # still waiting after a restart
    assert keep_on.runtime_data.keep_on_halted is True

    w = keep_on.runtime_data
    gave_up = next(fid for fid, f in w.findings.items() if "tries" in f.summary)
    await w.async_respond(gave_up, "will_check")
    assert w.keep_on_halted is False


async def test_switched_itself_off_is_quiet_when_off_on_purpose(hass: HomeAssistant, setup):
    entry, _ = setup
    entry.runtime_data.set_mode(MODE_OFF_ON_PURPOSE)
    hass.states.async_set(RELAY, "off", context=Context())
    await hass.async_block_till_done()
    assert _issues(hass) == []


async def test_i_switch_it_by_hand_stops_switched_itself_off(hass: HomeAssistant, setup):
    entry, _ = setup
    hass.states.async_set(RELAY, "off", context=Context())  # its button
    await hass.async_block_till_done()
    fid = next(iter(entry.runtime_data.findings))
    assert "switched_by_hand" in entry.runtime_data.offers(fid)
    await entry.runtime_data.async_respond(fid, "switched_by_hand")
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.options[CONF_HA_ONLY] is False  # and the entry reloaded with it

    hass.states.async_set(RELAY, "on", context=Context())
    hass.states.async_set(RELAY, "off", context=Context())
    await hass.async_block_till_done()
    assert _issues(hass) == []


# ---- what is watched, and suppressions that lapse --------------------------------

def _checks(hass: HomeAssistant, name: str = "kettle") -> dict[str, str]:
    return hass.states.get(f"sensor.{name}_guard_status").attributes["checks"]


async def test_status_shows_what_is_watched_and_why_not(hass: HomeAssistant, setup):
    entry, _ = setup
    assert _checks(hass) == {"over the plug's rating": "watching",
                             "plug switched itself off": "watching",
                             "plug came back off after a dropout": "watching"}
    entry.runtime_data.set_mode(MODE_OFF_ON_PURPOSE)
    assert _checks(hass)["plug came back off after a dropout"] == "off on purpose"
    assert _checks(hass)["over the plug's rating"] == "watching"


async def test_status_names_missing_settings(hass: HomeAssistant, dead_freezer):
    assert _checks(hass, "freezer") == {
        "over the plug's rating": "no plug rating set",
        "plug switched itself off": "no relay switch set",
        "plug came back off after a dropout": "no relay switch set",
        "failed starts": "watching",
        "not starting": "watching",
    }


async def test_off_on_purpose_ends_when_switched_back_on(hass: HomeAssistant, setup):
    entry, _ = setup
    w = entry.runtime_data
    w.set_mode(MODE_OFF_ON_PURPOSE)
    hass.states.async_set(RELAY, "unavailable")  # a dropout is not "back on"
    hass.states.async_set(RELAY, "on")
    await hass.async_block_till_done()
    assert w.mode == MODE_OFF_ON_PURPOSE
    hass.states.async_set(RELAY, "off")
    hass.states.async_set(RELAY, "on")
    await hass.async_block_till_done()
    assert w.mode == "watching"


async def test_off_on_purpose_ends_for_a_tiny_always_on_load(hass: HomeAssistant, monkeypatch):
    """A 3 W media converter: drawing again is above half its floor, not above 5 W."""
    from custom_components.appliance_guard import watcher as watcher_module

    now = dt_util.utcnow().timestamp()
    readings = [(now - 2 * 86400 + 600 * i, 3.0 + (i % 2) * 0.2) for i in range(280)]

    async def fake_load(_hass, _entity, _days):
        return readings

    monkeypatch.setattr(watcher_module, "async_load_readings", fake_load)
    hass.states.async_set(POWER, "3", {"device_class": "power", "unit_of_measurement": "W"})
    entry = MockConfigEntry(domain=DOMAIN, title="Fiber", data={CONF_POWER_ENTITY: POWER},
                            options={CONF_NAME: "Fiber", CONF_PROFILE: "always_on",
                                     CONF_CRITICAL: True}, unique_id=POWER)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    w = entry.runtime_data
    assert w.floor_w is not None and w.floor_w < 5
    w.set_mode(MODE_OFF_ON_PURPOSE)
    hass.states.async_set(POWER, "0")
    await hass.async_block_till_done()
    assert w.mode == MODE_OFF_ON_PURPOSE
    hass.states.async_set(POWER, "3.1")
    await hass.async_block_till_done()
    assert w.mode == "watching"
