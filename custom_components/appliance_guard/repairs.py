"""Repairs fix flow: answer a finding from Settings > Repairs."""

from __future__ import annotations

from typing import Any

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .const import RESPONSES
from .watcher import CHECK_TITLES, Watcher


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str,
                                data: dict[str, Any] | None) -> RepairsFlow:
    return FindingFixFlow(data or {})


class FindingFixFlow(RepairsFlow):
    def __init__(self, data: dict[str, Any]) -> None:
        self._entry_id = data.get("entry_id")
        self._fid = data.get("finding_id")

    def _watcher(self) -> Watcher | None:
        entry = self.hass.config_entries.async_get_entry(self._entry_id or "")
        return getattr(entry, "runtime_data", None) if entry else None

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        watcher = self._watcher()
        if watcher is None or self._fid not in watcher.findings:
            return self.async_abort(reason="gone")
        f = watcher.findings[self._fid]
        return self.async_show_menu(
            step_id="init",
            menu_options=watcher.offers(self._fid),
            description_placeholders={
                "name": watcher.cfg.name,
                "check": CHECK_TITLES.get(f.check, f.check),
                "summary": f.summary,
            },
        )

    async def _respond(self, response: str) -> FlowResult:
        watcher = self._watcher()
        if watcher is None:
            return self.async_abort(reason="gone")
        await watcher.async_respond(self._fid, response)
        return self.async_create_entry(data={})


def _make_step(response: str):
    async def step(self: FindingFixFlow, user_input: dict[str, Any] | None = None) -> FlowResult:
        return await self._respond(response)
    return step


for _r in RESPONSES:
    setattr(FindingFixFlow, f"async_step_{_r}", _make_step(_r))
