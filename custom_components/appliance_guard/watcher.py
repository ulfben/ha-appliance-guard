"""Home Assistant runtime for one watched plug: states in, findings out, answers back."""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EVENT_CALL_SERVICE,
    EVENT_HOMEASSISTANT_STOP,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import Context, Event, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store

from .const import (
    ACTION_PREFIX,
    ALARM_REPEAT_S,
    ALARM_REPEATS,
    COMMAND_WINDOW_S,
    CONF_CRITICAL,
    CONF_KEEP_ON,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_POWER_ENTITY,
    CONF_PROFILE,
    CONF_RATING_W,
    CONF_RELAY_ENTITY,
    DOMAIN,
    EVENT_FINDING,
    HISTORY_DAYS,
    KEEP_ON_AFTER_A1_S,
    KEEP_ON_TRIES,
    KEEP_ON_VERIFY_S,
    KEEP_ON_WINDOW_S,
    MODE_DISABLED,
    MODE_OFF_ON_PURPOSE,
    MODE_PAUSED,
    MODE_WATCHING,
    NEEDS_RELAY,
    OFF_ON_PURPOSE_RUNS,
    R_CUT_POWER,
    R_NEW_NORMAL,
    R_OFF_ON_PURPOSE,
    R_SNOOZE_1H,
    R_SNOOZE_24H,
    R_SOMETHING_NEW,
    R_TURN_BACK_ON,
    REPLAY_HOURS,
    TICK_S,
)
from .detector import (
    DeviceConfig,
    DeviceDetector,
    Dropout,
    Finding,
    Profile,
    Reported,
    Severity,
)
from .episodes import IDLE, RUN
from .learn import async_load_readings, async_load_relay, to_watts
from .model import (
    Model,
    NoSteadyDrawError,
    NotCyclingError,
    learn_cycling,
    learn_floor,
    without,
)

_LOGGER = logging.getLogger(__name__)

CHECK_TITLES = {
    "A1": "over the plug's rating",
    "A2": "plug switched itself off",
    "A3": "failed starts",
    "A5": "plug came back off after a dropout",
    "N1": "running far longer than normal",
    "N2": "not starting",
    "N4": "drawing more than usual between runs",
    "N5": "switched off from Home Assistant",
    "S2": "stopped drawing power",
}
# Safety alarms come through even while paused or snoozed.
SAFETY = {"A1", "A2", "A3", "A5"}
# Quiet while the appliance is off on purpose.
OFF_CHECKS = ("N2", "A5", "N5", "S2")
KEEP_ON_DELAY_S = 10  # lets an overload reading that raced the relay arrive first
STORE_VERSION = 1
SAVE_DELAY_S = 1
# Where each check keeps the start of the episode it is about.
EPISODE_START = {"N1": "run_start", "N2": "pause_start", "S2": "since"}
LEARN_RETRY_H = 1  # while there is nothing learned yet


class Watcher:
    """Owns the detector for one plug and everything around it."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        o = {**entry.data, **entry.options}
        self.power_entity: str = o[CONF_POWER_ENTITY]
        self.relay_entity: str | None = o.get(CONF_RELAY_ENTITY) or None
        self.cfg = DeviceConfig(
            name=o[CONF_NAME],
            profile=Profile(o[CONF_PROFILE]),
            rating_w=float(o[CONF_RATING_W]) if o.get(CONF_RATING_W) else None,
            critical=bool(o.get(CONF_CRITICAL, False)),
        )
        self.notify_targets: list[str] = list(o.get(CONF_NOTIFY) or [])
        self.keep_on = bool(o.get(CONF_KEEP_ON, False)) and self.relay_entity is not None
        self._keep_on_tries: deque[float] = deque()
        self._last_a1: float | None = None
        self.model: Model | None = None
        self.floor_w: float | None = None
        self.learn_note: str | None = None
        self.learned = False
        self.detector = DeviceDetector(self.cfg)
        self.mode = MODE_WATCHING
        self.snooze_until: float | None = None
        self.findings: dict[str, Finding] = {}
        self.last_finding: Finding | None = None
        self._sent: dict[str, tuple[float, int]] = {}  # finding id -> (last sent, times)
        self._commands: deque[tuple[float, str]] = deque(maxlen=100)
        self._purpose_runs = 0
        self._listeners: list[Callable[[], None]] = []
        self._event_listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._unsub_stop: Callable[[], None] | None = None
        # Set up while HA is starting: the relay's current state may be news (A5).
        self._at_boot = not hass.is_running
        self._store: Store[dict[str, Any]] = Store(
            hass, STORE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self.dropout_record: dict[str, Any] = {"back_on": 0, "back_off": 0, "last": None}
        # What was raised for the episodes going on at the last save, until relearned.
        self._stored_reported: list[Reported] = []
        # What was learned is kept until the user says otherwise, so a fault never
        # becomes "normal" by itself. Learning only looks at history after this time.
        self.learn_from: float | None = None

    # ---- lifecycle --------------------------------------------------------------

    async def async_start(self) -> None:
        self.entry.async_create_background_task(
            self.hass, self._async_begin(), f"{DOMAIN} start {self.cfg.name}")

    async def _async_begin(self) -> None:
        if (stored := await self._store.async_load()) is not None:
            self._restore(stored)
        await self.async_relearn()
        hass = self.hass
        self._unsub_stop = hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop)
        self._unsubs += [
            self._unsub_stop,
            async_track_state_change_event(hass, [self.power_entity], self._on_power),
            async_track_time_interval(hass, self._on_tick, timedelta(seconds=TICK_S)),
            async_track_time_interval(hass, self._on_learn_retry,
                                      timedelta(hours=LEARN_RETRY_H)),
            hass.bus.async_listen(EVENT_CALL_SERVICE, self._on_call_service),
            hass.bus.async_listen("mobile_app_notification_action", self._on_phone_action),
        ]
        if self.relay_entity:
            self._unsubs.append(
                async_track_state_change_event(hass, [self.relay_entity], self._on_relay))

    async def async_stop(self) -> None:
        """Unloading (reload, options saved, removed): save, then take the issues down.

        They come back from the store when the entry is set up again.
        """
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._store.async_save(self._stored())
        for fid in list(self.findings):
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id(fid))

    @callback
    def _on_ha_stop(self, _event: Event) -> None:
        # A listen_once listener must not be removed after it has fired.
        self._unsubs = [u for u in self._unsubs if u is not self._unsub_stop]
        self._save()  # written at the final write, with the episodes as they are then

    # ---- what survives a restart ----------------------------------------------

    def _restore(self, stored: dict[str, Any]) -> None:
        self.dropout_record |= stored.get("dropouts", {})
        self.snooze_until = stored.get("snooze_until")
        for fid, d in stored.get("findings", {}).items():
            f = Finding(d["check"], Severity(d["severity"]), d["ts"], d["summary"],
                        d.get("data", {}))
            self.findings[fid] = f
            self.last_finding = f
            if sent := d.get("sent"):
                self._sent[fid] = (sent[0], sent[1])
            self._raise_issue(fid, f)  # no phone: it was told when the finding was new
        self._stored_reported = [Reported(**r) for r in stored.get("reported", [])]
        self._restore_baseline(stored.get("baseline") or {})

    def _restore_baseline(self, b: dict[str, Any]) -> None:
        self.learn_from = b.get("learn_from")
        if b.get("profile") != self.cfg.profile.value:
            return  # the profile was changed: learn again
        try:
            self.model = Model(**b["model"]) if b.get("model") else None
        except TypeError:
            self.model = None  # stored by a version with other fields
        self.floor_w = b.get("floor_w")

    def _stored(self) -> dict[str, Any]:
        findings = {}
        for fid, f in self.findings.items():
            findings[fid] = {"check": f.check, "severity": f.severity.value, "ts": f.ts,
                             "summary": f.summary, "data": f.data}
            if fid in self._sent:
                findings[fid]["sent"] = list(self._sent[fid])
        return {
            "dropouts": self.dropout_record,
            "findings": findings,
            "reported": [vars(r) for r in self._reported()],
            "snooze_until": self.snooze_until,
            "baseline": {"profile": self.cfg.profile.value,
                         "model": vars(self.model) if self.model else None,
                         "floor_w": self.floor_w,
                         "learn_from": self.learn_from},
        }

    def _save(self) -> None:
        self._store.async_delay_save(self._stored, SAVE_DELAY_S)

    def _reported(self) -> list[Reported]:
        """Raised for the episodes going on now: open findings, and answered ones."""
        if self.learned:
            out = self.detector.reported(time.time())
        else:
            out = list(self._stored_reported)
        for f in self.findings.values():
            if (key := EPISODE_START.get(f.check)) and key in f.data:
                out.append(Reported(f.check, f.severity.value, f.data[key], f.ts))
        latest: dict[tuple[str, str, float], Reported] = {}
        for r in sorted(out, key=lambda r: r.seen_at):
            latest[(r.check, r.severity, r.start)] = r
        return list(latest.values())

    @property
    def has_baseline(self) -> bool:
        """Something learned that the checks can use (on-demand plugs need nothing)."""
        if self.cfg.profile == Profile.CYCLING:
            return self.model is not None
        if self.cfg.profile == Profile.ALWAYS_ON:
            return self.floor_w is not None
        return True

    async def _on_learn_retry(self, _now: datetime) -> None:
        if not self.has_baseline:
            await self.async_relearn()

    async def async_relearn(self, *, fresh: bool = False) -> None:
        """Set up the detector: learn if nothing is kept (or `fresh`), replay the last hours.

        Learning leaves out time the plug was switched off, and only looks at history
        after `learn_from`.
        """
        readings = await async_load_readings(self.hass, self.power_entity, HISTORY_DAYS)
        relay = (await async_load_relay(self.hass, self.relay_entity, HISTORY_DAYS)
                 if self.relay_entity else ([], []))
        if fresh:
            self.model, self.floor_w = None, None
        if not self.has_baseline:
            await self._async_learn(readings, relay[0])
        det = DeviceDetector(self.cfg, self.model, floor_w=self.floor_w)
        cutoff = time.time() - REPLAY_HOURS * 3600
        for ts, watts in readings:
            if ts >= cutoff:
                det.feed(ts, watts)  # findings about the past are not reported
        # A problem still going on that nobody was told about is news. One already
        # raised for the same episode is not, whether it is still open or was answered.
        det.resume(self._reported())
        findings = self._seed_relay(det, *relay)
        findings += det.tick(time.time())
        self.detector, self.learned = det, True
        self._save()
        self._handle(findings)
        self._update()

    async def _async_learn(self, readings: list[tuple[float, float | None]],
                           relay_states: list[tuple[float, str]]) -> None:
        now = time.time()
        readings = without(readings, _off_spans(relay_states, now))
        if self.learn_from is not None:
            readings = [r for r in readings if r[0] >= self.learn_from]
        self.learn_note = None
        try:
            if self.cfg.profile == Profile.CYCLING:
                self.model = await self.hass.async_add_executor_job(learn_cycling, readings)
            elif self.cfg.profile == Profile.ALWAYS_ON:
                self.floor_w = await self.hass.async_add_executor_job(learn_floor, readings)
        except (NotCyclingError, NoSteadyDrawError) as err:
            self.learn_note = str(err)
        _LOGGER.debug("%s learned: %s %s (%s)", self.cfg.name, self.model, self.floor_w,
                      self.learn_note)

    def _widen(self, f: Finding | None) -> None:
        """'This is normal for it': allow what the finding was about from now on."""
        m = self.model
        if f is None or m is None:
            return
        seg = self.detector.seg
        cur = seg.current if seg is not None else None
        ongoing = (seg.known_duration(time.time())
                   if cur is not None and cur.start == f.data.get(EPISODE_START.get(f.check))
                   else 0.0)
        dur = max(float(f.data.get("duration_s", 0.0)), ongoing)
        if f.check == "N1":
            m = replace(m, max_run_s=max(m.max_run_s, dur), p99_run_s=max(m.p99_run_s, dur))
        elif f.check == "N2":
            m = replace(m, max_pause_s=max(m.max_pause_s, dur),
                        p99_pause_s=max(m.p99_pause_s, dur))
        elif f.check == "N4" and "watts" in f.data:
            m = replace(m, pause_level_p90=max(m.pause_level_p90, float(f.data["watts"])))
        self.model = self.detector.model = m

    def _seed_relay(self, det: DeviceDetector, states: list[tuple[float, str]],
                    stops: list[float]) -> list[Finding]:
        """Give the detector the relay's last real state, kept through gaps and restarts.

        Right after HA starts, the state the plug came back with is news: a plug
        that was on before the restart and is off now lost power (A5). Any other
        time, the current state is just where things stand.
        """
        if not self.relay_entity:
            return []
        boot, self._at_boot = self._at_boot, False
        last_stop = max(stops, default=None)
        events = sorted([(t, _relay_on(v)) for t, v in states]
                        + [(t, "stop") for t in stops], key=lambda e: e[0])
        for ts, on in events:
            if boot and last_stop is not None and ts > last_stop:
                break  # from here on it is this start's news
            if on == "stop":
                det.feed_ha_down(ts)
            else:
                det.feed_relay(ts, on, commanded=True)
        det.dropouts.clear()  # the past was not watched live
        st = self.hass.states.get(self.relay_entity)
        if st is None:
            return []
        live = boot and last_stop is not None
        return det.feed_relay(st.last_updated_timestamp, _relay_on(st.state),
                              commanded=not live or self._commanded(st.context))

    # ---- inputs -----------------------------------------------------------------

    @callback
    def _on_power(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None:
            return
        watts = to_watts(new)
        before = self._seg_state()
        findings = self.detector.feed(new.last_updated_timestamp, watts)
        self._track_purpose(before, watts)
        self._handle(findings)
        self._collect_dropouts()

    @callback
    def _on_relay(self, event: Event) -> None:
        new = event.data.get("new_state")
        if new is None:
            return
        findings = self.detector.feed_relay(
            new.last_updated_timestamp, _relay_on(new.state), self._commanded(new.context))
        self._handle(findings)
        self._collect_dropouts()

    @callback
    def _on_call_service(self, event: Event) -> None:
        self._commands.append((time.time(), event.context.id))

    def _commanded(self, ctx: Context) -> bool:
        """Was this state change caused by a service call (UI, automation, integration)?"""
        if ctx.user_id or ctx.parent_id:
            return True
        now = time.time()
        return any(cid == ctx.id and now - t <= COMMAND_WINDOW_S for t, cid in self._commands)

    @callback
    def _on_tick(self, now: datetime) -> None:
        self._handle(self.detector.tick(now.timestamp()))
        self._collect_dropouts()
        self._repeat_alarms(now.timestamp())

    # ---- what the plug does after a dropout ---------------------------------------

    def _collect_dropouts(self) -> None:
        seen: list[Dropout] = self.detector.dropouts
        if not seen:
            return
        rec = self.dropout_record
        for d in seen:
            rec["back_" + d.outcome] += 1
            rec["last"] = {"ts": d.ts, "outcome": d.outcome, "gap_s": round(d.gap_s),
                           "ha_down": d.ha_down}
        seen.clear()
        self._save()
        self._update()

    @property
    def after_dropout(self) -> str:
        """One "came back off" settles it: the plug can't be trusted to restore power."""
        rec = self.dropout_record
        if rec["back_off"]:
            return "came_back_off"
        return "came_back_on" if rec["back_on"] else "not_seen"

    # ---- findings ----------------------------------------------------------------

    def _allowed(self, f: Finding) -> bool:
        if self.mode == MODE_DISABLED:
            return False
        quiet = self.mode == MODE_PAUSED or (
            self.snooze_until is not None and time.time() < self.snooze_until)
        if quiet and not (f.severity == Severity.ALARM and f.check in SAFETY):
            return False
        return not (self.mode == MODE_OFF_ON_PURPOSE and f.check in OFF_CHECKS)

    @callback
    def _handle(self, findings: list[Finding]) -> None:
        if findings:
            self._save()  # suppressed ones too: they count as raised for their episode
        changed = False
        for f in findings:
            if f.check == "A1":
                self._last_a1 = f.ts
            if not self._allowed(f):
                _LOGGER.debug("%s: suppressed %s (%s)", self.cfg.name, f.check, self.mode)
                continue
            if self._keeps_on(f):
                async_call_later(self.hass, KEEP_ON_DELAY_S,
                                 callback(lambda _now, f=f: self._keep_on_now(f)))
                f = replace(f, severity=Severity.NOTICE,
                            summary=f"{f.summary} Turning it back on.",
                            data={**f.data, "kept_on": True,
                                  "offer": ["off_on_purpose", "will_check"]})
            fid = f"{f.check}_{int(f.ts)}"
            self.findings[fid] = f
            self.last_finding = f
            changed = True
            self._raise_issue(fid, f)
            payload = {"entry_id": self.entry.entry_id, "finding_id": fid, "check": f.check,
                       "severity": f.severity.value, "summary": f.summary}
            self.hass.bus.async_fire(EVENT_FINDING, payload)
            self._emit(f.severity.value, payload)
            self._send_phone(fid, f)
        if changed:
            self._update()

    def _raise_issue(self, fid: str, f: Finding) -> None:
        ir.async_create_issue(
            self.hass, DOMAIN, self.issue_id(fid),
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.ERROR if f.severity == Severity.ALARM
            else ir.IssueSeverity.WARNING,
            translation_key="finding",
            translation_placeholders={
                "name": self.cfg.name,
                "check": CHECK_TITLES.get(f.check, f.check),
                "severity": f.severity.value,
                "summary": f.summary,
            },
            data={"entry_id": self.entry.entry_id, "finding_id": fid},
        )

    # ---- keep powered ------------------------------------------------------------

    def _keeps_on(self, f: Finding) -> bool:
        return (self.keep_on and f.check in ("A2", "A5") and self.mode == MODE_WATCHING
                and "kept_on" not in f.data)

    def _keep_on_refusal(self, now: float) -> str | None:
        if self._last_a1 is not None and now - self._last_a1 < KEEP_ON_AFTER_A1_S:
            return "it was just over its rating, so its overload protection may have tripped"
        while self._keep_on_tries and now - self._keep_on_tries[0] > KEEP_ON_WINDOW_S:
            self._keep_on_tries.popleft()
        if len(self._keep_on_tries) >= KEEP_ON_TRIES:
            return f"it was already turned back on {KEEP_ON_TRIES} times in the last hour"
        if self.mode != MODE_WATCHING:
            return "it is no longer being watched"
        return None

    def _relay_is_on(self) -> bool:
        st = self.hass.states.get(self.relay_entity) if self.relay_entity else None
        return st is not None and st.state == STATE_ON

    @callback
    def _keep_on_now(self, f: Finding) -> None:
        if self._relay_is_on():
            return
        now = time.time()
        if (why := self._keep_on_refusal(now)) is not None:
            self._give_up(f, f"Not turned back on: {why}.")
            return
        self._keep_on_tries.append(now)
        self.hass.async_create_task(self._async_keep_on(f))

    async def _async_keep_on(self, f: Finding) -> None:
        try:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self.relay_entity}, blocking=True)
        except Exception:  # noqa: BLE001 - a plug that is still booting can refuse
            _LOGGER.warning("%s: could not turn the plug back on", self.cfg.name,
                            exc_info=True)
        async_call_later(self.hass, KEEP_ON_VERIFY_S,
                         callback(lambda _now: self._keep_on_check(f)))

    @callback
    def _keep_on_check(self, f: Finding) -> None:
        if self._relay_is_on():
            _LOGGER.info("%s: turned back on", self.cfg.name)
            return
        if self._keep_on_refusal(time.time()) is None:
            self._keep_on_now(f)  # try again
            return
        self._give_up(f, f"It did not come back on after {len(self._keep_on_tries)} tries.")

    def _give_up(self, f: Finding, why: str) -> None:
        self._handle([replace(f, ts=time.time(), summary=f"{f.summary} {why}",
                              data={**f.data, "kept_on": False})])

    def issue_id(self, fid: str) -> str:
        return f"{self.entry.entry_id}_{fid}"

    def offers(self, fid: str) -> list[str]:
        f = self.findings.get(fid)
        base = list(f.data.get("offer", [])) if f else []
        base += [R_SNOOZE_1H, R_SNOOZE_24H]
        return [r for r in dict.fromkeys(base) if r not in NEEDS_RELAY or self.relay_entity]

    async def async_respond(self, fid: str, response: str) -> None:
        f = self.findings.pop(fid, None)
        self._sent.pop(fid, None)
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id(fid))
        now = time.time()
        if response == R_OFF_ON_PURPOSE:
            self.set_mode(MODE_OFF_ON_PURPOSE)
        elif response == R_SOMETHING_NEW:
            # A different appliance: what was learned is about the old one.
            self.learn_from = now
            await self.async_relearn(fresh=True)
        elif response == R_NEW_NORMAL:
            self._widen(f)
        elif response == R_SNOOZE_1H:
            self.snooze_until = now + 3600
        elif response == R_SNOOZE_24H:
            self.snooze_until = now + 86400
        elif response == R_CUT_POWER and self.relay_entity:
            self.set_mode(MODE_OFF_ON_PURPOSE)
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self.relay_entity}, blocking=True)
        elif response == R_TURN_BACK_ON and self.relay_entity:
            await self.hass.services.async_call(
                "switch", "turn_on", {"entity_id": self.relay_entity}, blocking=True)
        self._save()
        _LOGGER.info("%s: %s answered with %s", self.cfg.name, fid, response)
        self._emit("response", {"entry_id": self.entry.entry_id, "finding_id": fid,
                                "check": f.check if f else None, "response": response})
        self._update()

    # ---- modes -------------------------------------------------------------------

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self._purpose_runs = 0
        self._update()

    def _seg_state(self) -> str | None:
        seg = self.detector.seg
        return seg.current.state if seg is not None and seg.current is not None else None

    def _track_purpose(self, before: str | None, watts: float | None) -> None:
        """'Off on purpose' lapses when the device runs normally again."""
        if self.mode != MODE_OFF_ON_PURPOSE or watts is None:
            return
        if self.detector.seg is None:  # not cycling: any real draw counts
            if watts > 5:
                self.set_mode(MODE_WATCHING)
            return
        if before == IDLE and self._seg_state() == RUN:
            self._purpose_runs += 1
            if self._purpose_runs >= OFF_ON_PURPOSE_RUNS:
                self.set_mode(MODE_WATCHING)

    # ---- phone notifications -------------------------------------------------------

    def _send_phone(self, fid: str, f: Finding) -> None:
        if not self.notify_targets:
            return
        last, times = self._sent.get(fid, (0.0, 0))
        self._sent[fid] = (time.time(), times + 1)
        self._save()
        actions = [
            {"action": f"{ACTION_PREFIX}|{self.entry.entry_id}|{fid}|{r}",
             "title": RESPONSE_TITLES.get(r, r)}
            for r in self.offers(fid)[:2]
        ]
        actions.append({"action": "URI", "title": "More…", "uri": "/config/repairs"})
        data = {"tag": self.issue_id(fid), "actions": actions}
        if f.severity == Severity.ALARM:
            data |= {"priority": "high", "ttl": 0,
                     "push": {"interruption-level": "time-sensitive"}}
        title = f"{self.cfg.name}: {'alarm' if f.severity == Severity.ALARM else 'notice'}"
        for target in self.notify_targets:
            self.hass.async_create_task(self._async_notify(target, title, f.summary, data))

    async def _async_notify(self, target: str, title: str, message: str, data: dict) -> None:
        try:
            await self.hass.services.async_call(
                "notify", target, {"title": title, "message": message, "data": data},
                blocking=True)
        except Exception:  # noqa: BLE001 - a missing phone must not break detection
            _LOGGER.warning("Could not notify %s", target, exc_info=True)

    def _repeat_alarms(self, now: float) -> None:
        for fid, f in list(self.findings.items()):
            if f.severity != Severity.ALARM or fid not in self._sent:
                continue
            last, times = self._sent[fid]
            if times <= ALARM_REPEATS and now - last >= ALARM_REPEAT_S:
                self._send_phone(fid, f)

    @callback
    def _on_phone_action(self, event: Event) -> None:
        parts = str(event.data.get("action", "")).split("|")
        if len(parts) == 4 and parts[0] == ACTION_PREFIX and parts[1] == self.entry.entry_id:
            self.hass.async_create_task(self.async_respond(parts[2], parts[3]))

    # ---- listeners -----------------------------------------------------------------

    @property
    def status(self) -> str:
        if self.mode == MODE_DISABLED:
            return "disabled"
        if any(f.severity == Severity.ALARM for f in self.findings.values()):
            return "alarm"
        if self.findings:
            return "notice"
        if self.mode == MODE_PAUSED or (self.snooze_until and time.time() < self.snooze_until):
            return "paused"
        if self.mode == MODE_OFF_ON_PURPOSE:
            return "off_on_purpose"
        if not self.learned or (self.cfg.profile == Profile.CYCLING and self.model is None):
            return "learning"
        return "ok"

    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def async_add_event_listener(
            self, cb: Callable[[str, dict[str, Any]], None]) -> Callable[[], None]:
        self._event_listeners.append(cb)
        return lambda: self._event_listeners.remove(cb)

    def _update(self) -> None:
        for cb in list(self._listeners):
            cb()

    def _emit(self, kind: str, data: dict[str, Any]) -> None:
        for cb in list(self._event_listeners):
            cb(kind, data)


RESPONSE_TITLES = {
    "off_on_purpose": "Off on purpose",
    "something_new": "Something new",
    "new_normal": "New normal",
    "one_off": "One-off, fine",
    "will_check": "I'll check",
    "snooze_1h": "Snooze 1 h",
    "snooze_24h": "Snooze 24 h",
    "cut_power": "Cut power",
    "turn_back_on": "Turn back on",
}


def _off_spans(states: list[tuple[float, str]], now: float) -> list[tuple[float, float]]:
    """When the relay was switched off: the appliance had no power, so learn nothing."""
    out, since = [], None
    for ts, state in states:
        if state == STATE_OFF:
            since = ts if since is None else since
        elif since is not None:
            out.append((since, ts))
            since = None
    if since is not None:
        out.append((since, now))
    return out


def _relay_on(state: str) -> bool | None:
    return {STATE_ON: True, STATE_OFF: False}.get(state)
