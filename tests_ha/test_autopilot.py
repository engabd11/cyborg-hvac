"""Autopilot on the demo unit: balancing acts, manual changes pause it."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant

from custom_components.cyborg_hvac.const import DOMAIN
from custom_components.cyborg_hvac.core.model import HvacMode

from .test_comfort import set_comfort
from .test_demo import setup_demo


async def _tick(hass: HomeAssistant, comfort) -> None:
    # Let the autopilot gap pass and evaluate again.
    for unit_id in list(comfort._last_run):
        comfort._last_run[unit_id] -= timedelta(minutes=2)
    await comfort.async_refresh()
    await hass.async_block_till_done()


async def test_room_balancing_moves_dampers_and_respects_manual_changes(
    hass: HomeAssistant,
) -> None:
    entry = await setup_demo(hass)
    await set_comfort(hass, entry)
    comfort = entry.runtime_data.comfort
    backend = entry.runtime_data.backend
    system = backend.system
    system.power, system.mode, system.target = True, HvacMode.HEAT, 23.0
    system.zones[1].temperature = 25.0  # well above the band: should throttle
    system.zones[2].temperature = 19.0
    system.zones[3].temperature = 19.0
    await entry.runtime_data.coordinator.async_refresh()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.demo_ducted_unit_climate_butler"},
        blocking=True,
    )
    await _tick(hass, comfort)
    # Ahead of the others while they heat: closed in one write, not throttled in steps.
    assert system.zones[1].on is False
    status = comfort.autopilot_status("demo_unit")
    assert status["behaviours"]["room_balancing"] is True
    assert status["last_action"] is not None
    assert status["last_action"]["reason"]
    # Autopilot's own change is not mistaken for a person's.
    assert "1" not in status["zones_paused"]
    assert status["decision"]["roles"]["1"] == "shed"
    payload = comfort.as_dict("demo_unit")
    assert payload["zones"][0]["role"] == "shed"
    assert "season" in payload["house"]

    # A person moves zone 2's damper: autopilot leaves it alone for a while.
    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": "cover.office", "position": 35}, blocking=True
    )
    assert "2" in comfort.autopilot_status("demo_unit")["zones_paused"]
    system.zones[2].temperature = 17.0
    await _tick(hass, comfort)
    assert system.zones[2].opening == 35

    await hass.services.async_call(DOMAIN, "resume_autopilot", {}, blocking=True)
    assert comfort.autopilot_status("demo_unit")["zones_paused"] == {}


async def test_pause_holds_everything(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    comfort = entry.runtime_data.comfort
    await hass.services.async_call(DOMAIN, "pause_autopilot", {"minutes": 30}, blocking=True)
    status = comfort.autopilot_status("demo_unit")
    assert status["paused_until"] is not None
    assert len(status["zones_paused"]) == 8
