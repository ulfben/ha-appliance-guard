"""Config flow: pick the plugs to watch. One entry per watched plug."""

from __future__ import annotations

import asyncio
import re
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_INTEGRATION_DISCOVERY,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.util import slugify

from .const import (
    CONF_CRITICAL,
    CONF_HA_ONLY,
    CONF_KEEP_ON,
    CONF_NAME,
    CONF_NOTIFY,
    CONF_POWER_ENTITY,
    CONF_PROFILE,
    CONF_RATING_W,
    CONF_RELAY_ENTITY,
    CRITICAL_WORDS,
    DOMAIN,
    HISTORY_DAYS,
    RATING_BY_MODEL,
)
from .learn import async_load_readings
from .model import PlugSummary, summarize

PROFILES = ["cycling", "always_on", "on_demand"]
CONF_PLUGS = "plugs"
_NOT_RELAY = re.compile(r"child_lock|led|outage|lock|indicator|backlight")


def _relays(ent_reg: er.EntityRegistry, device_id: str) -> list[str]:
    return [e.entity_id for e in er.async_entries_for_device(ent_reg, device_id)
            if e.domain == "switch" and not _NOT_RELAY.search(e.entity_id)]


def guess_defaults(hass: HomeAssistant, power_entity: str) -> dict[str, Any]:
    """Name, relay and rating from the plug's device."""
    ent_reg, dev_reg = er.async_get(hass), dr.async_get(hass)
    entry = ent_reg.async_get(power_entity)
    state = hass.states.get(power_entity)
    name = state.name if state else power_entity
    out: dict[str, Any] = {CONF_RELAY_ENTITY: None, CONF_RATING_W: None}
    if entry and entry.device_id and (dev := dev_reg.async_get(entry.device_id)):
        name = dev.name_by_user or dev.name or name
        relays = _relays(ent_reg, dev.id)
        out[CONF_RELAY_ENTITY] = relays[0] if relays else None
        for key, watts in RATING_BY_MODEL.items():
            if key.lower() in f"{dev.model or ''} {dev.model_id or ''}".lower():
                out[CONF_RATING_W] = watts
                break
    name = re.sub(r"^power to\s+", "", name, flags=re.I).strip()
    out[CONF_NAME] = name[:1].upper() + name[1:]
    out[CONF_CRITICAL] = any(w in name.lower() for w in CRITICAL_WORDS)
    return out


def find_plugs(hass: HomeAssistant, skip: set[str]) -> list[str]:
    """Power sensors on a device that also has a relay switch: smart plugs."""
    ent_reg = er.async_get(hass)
    found = []
    for e in list(ent_reg.entities.values()):
        if (e.domain != "sensor" or e.disabled_by or not e.device_id
                or (e.device_class or e.original_device_class) != "power"
                or e.entity_id in skip or hass.states.get(e.entity_id) is None):
            continue
        if _relays(ent_reg, e.device_id):
            found.append(e.entity_id)
    return sorted(found)


async def async_summarize(hass: HomeAssistant, power_entity: str
                          ) -> tuple[dict[str, Any], PlugSummary]:
    """Guessed settings and a history summary for one plug."""
    d = guess_defaults(hass, power_entity)
    readings = await async_load_readings(hass, power_entity, HISTORY_DAYS)
    summary = await hass.async_add_executor_job(
        summarize, readings, d[CONF_RATING_W], d[CONF_CRITICAL], time.time())
    d[CONF_PROFILE] = summary.profile
    return d, summary


def phone_names(hass: HomeAssistant) -> dict[str, str]:
    """notify service -> the phone's name in the Companion app, e.g. 'S24 Ultra'."""
    services = hass.services.async_services().get("notify", {})
    names = {s: s.removeprefix("mobile_app_") for s in services if s.startswith("mobile_app_")}
    for entry in hass.config_entries.async_entries("mobile_app"):
        name = entry.data.get("device_name") or entry.title
        if (service := f"mobile_app_{slugify(name)}") in names:
            names[service] = entry.title or name
    return names


def _notify_selector(hass: HomeAssistant) -> selector.SelectSelector:
    options = [selector.SelectOptionDict(value=s, label=n)
               for s, n in sorted(phone_names(hass).items(), key=lambda i: i[1].lower())]
    return selector.SelectSelector(
        selector.SelectSelectorConfig(options=options, multiple=True, custom_value=True))


def details_schema(hass: HomeAssistant, d: dict[str, Any]) -> vol.Schema:
    rating = {"default": d[CONF_RATING_W]} if d.get(CONF_RATING_W) else {}
    relay = {"default": d[CONF_RELAY_ENTITY]} if d.get(CONF_RELAY_ENTITY) else {}
    return vol.Schema({
        vol.Required(CONF_NAME, default=d.get(CONF_NAME, "")): str,
        vol.Required(CONF_PROFILE, default=d.get(CONF_PROFILE, "on_demand")):
            selector.SelectSelector(selector.SelectSelectorConfig(
                options=PROFILES, translation_key="profile",
                mode=selector.SelectSelectorMode.LIST)),
        vol.Optional(CONF_RATING_W, **rating): selector.NumberSelector(
            selector.NumberSelectorConfig(min=100, max=4000, step=10,
                                          unit_of_measurement=UnitOfPower.WATT,
                                          mode=selector.NumberSelectorMode.BOX)),
        vol.Required(CONF_CRITICAL, default=bool(d.get(CONF_CRITICAL))): bool,
        vol.Required(CONF_HA_ONLY,
                     default=bool(d.get(CONF_HA_ONLY, d.get(CONF_CRITICAL)))): bool,
        vol.Required(CONF_KEEP_ON, default=bool(d.get(CONF_KEEP_ON))): bool,
        vol.Optional(CONF_RELAY_ENTITY, **relay): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")),
        vol.Optional(CONF_NOTIFY, default=d.get(CONF_NOTIFY) or []): _notify_selector(hass),
    })


class GuardConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._power: str | None = None
        self._defaults: dict[str, Any] = {}
        self._found: dict[str, tuple[dict[str, Any], PlugSummary]] = {}
        self._scan: asyncio.Task | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        """Offer the plugs found on this system, or a sensor picked by hand."""
        if not find_plugs(self.hass, set(self._async_current_ids())):
            return await self.async_step_manual()
        return self.async_show_menu(step_id="user", menu_options=["plugs", "manual"])

    async def async_step_plugs(self, user_input: dict[str, Any] | None = None
                               ) -> ConfigFlowResult:
        """Read every plug's history, showing a progress screen meanwhile."""
        if self._scan is None:
            plugs = find_plugs(self.hass, set(self._async_current_ids()))
            self._scan = self.hass.async_create_task(self._async_scan(plugs))
        if not self._scan.done():
            return self.async_show_progress(step_id="plugs", progress_action="scan",
                                            progress_task=self._scan)
        return self.async_show_progress_done(next_step_id="pick" if self._found else "manual")

    async def _async_scan(self, plugs: list[str]) -> None:
        found = await asyncio.gather(*(async_summarize(self.hass, p) for p in plugs))
        # Chargers, inverters and meters also sit next to a switch; drop them here.
        # They can still be picked by hand.
        self._found = {p: f for p, f in zip(plugs, found, strict=True) if f[1].socket}

    async def async_step_pick(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        """Tick the plugs to watch. Each becomes its own entry with the guessed settings."""
        if user_input is not None:
            chosen = user_input.get(CONF_PLUGS) or []
            if not chosen:
                return await self.async_step_manual()
            notify = user_input.get(CONF_NOTIFY) or []
            first, *rest = chosen
            for power in rest:
                self.hass.async_create_task(self.hass.config_entries.flow.async_init(
                    DOMAIN, context={"source": SOURCE_INTEGRATION_DISCOVERY},
                    data={CONF_POWER_ENTITY: power, "options": self._options(power, notify)}))
            await self.async_set_unique_id(first)
            self._abort_if_unique_id_configured()
            opts = self._options(first, notify)
            names = [self._found[p][0][CONF_NAME] for p in chosen]
            return self.async_create_entry(
                title=opts[CONF_NAME], data={CONF_POWER_ENTITY: first}, options=opts,
                description="picked" if rest else None,
                description_placeholders={"count": str(len(chosen)),
                                          "names": ", ".join(names)} if rest else None)
        ordered = sorted(self._found.items(),
                         key=lambda i: (not i[1][1].watch, i[1][0][CONF_NAME].lower()))
        options = [selector.SelectOptionDict(value=p, label=f"{d[CONF_NAME]}: {s.text}")
                   for p, (d, s) in ordered]
        suggested = [p for p, (_, s) in ordered if s.watch]
        return self.async_show_form(step_id="pick", data_schema=vol.Schema({
            vol.Optional(CONF_PLUGS, default=suggested): selector.SelectSelector(
                selector.SelectSelectorConfig(options=options, multiple=True,
                                              mode=selector.SelectSelectorMode.LIST)),
            vol.Optional(CONF_NOTIFY, default=[]): _notify_selector(self.hass),
        }))

    def _options(self, power: str, notify: list[str]) -> dict[str, Any]:
        d, _ = self._found[power]
        return {CONF_NAME: d[CONF_NAME], CONF_PROFILE: d[CONF_PROFILE],
                CONF_RATING_W: d[CONF_RATING_W], CONF_CRITICAL: d[CONF_CRITICAL],
                CONF_HA_ONLY: d[CONF_CRITICAL],
                CONF_RELAY_ENTITY: d[CONF_RELAY_ENTITY], CONF_NOTIFY: notify}

    async def async_step_integration_discovery(self, discovery_info: dict[str, Any]
                                               ) -> ConfigFlowResult:
        """A plug ticked in the picker: confirmed there already."""
        power = discovery_info[CONF_POWER_ENTITY]
        await self.async_set_unique_id(power)
        self._abort_if_unique_id_configured()
        opts = discovery_info["options"]
        return self.async_create_entry(title=opts[CONF_NAME],
                                       data={CONF_POWER_ENTITY: power}, options=opts)

    async def async_step_manual(self, user_input: dict[str, Any] | None = None
                                ) -> ConfigFlowResult:
        """Any power sensor, picked by hand, then the details form."""
        if user_input is not None:
            self._power = user_input[CONF_POWER_ENTITY]
            await self.async_set_unique_id(self._power)
            self._abort_if_unique_id_configured()
            self._defaults, _ = await async_summarize(self.hass, self._power)
            return await self.async_step_details()
        return self.async_show_form(step_id="manual", data_schema=vol.Schema({
            vol.Required(CONF_POWER_ENTITY): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", device_class="power")),
        }))

    async def async_step_details(self, user_input: dict[str, Any] | None = None
                                 ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                title=user_input[CONF_NAME],
                data={CONF_POWER_ENTITY: self._power},
                options=user_input,
            )
        return self.async_show_form(
            step_id="details",
            data_schema=details_schema(self.hass, self._defaults),
            description_placeholders={"profile": self._defaults.get(CONF_PROFILE, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return GuardOptionsFlow()


class GuardOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None
                              ) -> ConfigFlowResult:
        if user_input is not None:
            self.hass.config_entries.async_update_entry(self.config_entry,
                                                        title=user_input[CONF_NAME])
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=details_schema(self.hass, dict(self.config_entry.options)))
