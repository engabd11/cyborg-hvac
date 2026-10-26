"""The Climate Butler on the demo unit: energy, its activity, its journal and taking over."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.cyborg_hvac.const import DOMAIN

from .test_demo import setup_demo

ENERGY = "select.demo_ducted_unit_energy"
BUTLER = "switch.demo_ducted_unit_climate_butler"
ACTIVITY = "sensor.demo_ducted_unit_climate_butler_activity"


async def turn_on(hass: HomeAssistant, entity_id: str) -> None:
    await hass.services.async_call("switch", "turn_on", {"entity_id": entity_id}, blocking=True)
    await hass.async_block_till_done()


async def test_energy_select_and_activity_sensor(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    comfort = entry.runtime_data.comfort
    state = hass.states.get(ENERGY)
    assert state.state == "auto"
    assert state.attributes["active"] == "mid"  # no solar or battery sensors
    await hass.services.async_call(
        "select", "select_option", {"entity_id": ENERGY, "option": "max"}, blocking=True
    )
    await hass.async_block_till_done()
    state = hass.states.get(ENERGY)
    assert state.state == "max"
    assert state.attributes["active"] == "max"
    assert hass.states.get(ACTIVITY).state == "off"

    await turn_on(hass, BUTLER)
    activity = hass.states.get(ACTIVITY)
    assert activity.state not in ("off", "unavailable")
    assert activity.attributes["energy"] == "max"
    assert activity.attributes["headline"]
    headlines = [e.headline for e in comfort.journal("demo_unit").entries]
    assert "Energy set to Max" in headlines
    assert "Climate Butler on" in headlines


async def test_journal_pages_over_the_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await setup_demo(hass)
    await turn_on(hass, BUTLER)
    await entry.runtime_data.comfort.async_refresh()
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/journal", "limit": 50})
    result = (await client.receive_json())["result"]
    assert result["unit_id"] == "demo_unit"
    entries = result["entries"]
    assert entries[-1]["headline"] == "Climate Butler on"  # oldest last
    assert entries[-1]["kind"] == "switch"
    await client.send_json(
        {"id": 2, "type": f"{DOMAIN}/journal", "before": entries[-1]["time"], "limit": 5}
    )
    assert (await client.receive_json())["result"]["entries"] == []
    await client.send_json({"id": 3, "type": f"{DOMAIN}/journal", "before": "yesterday"})
    assert not (await client.receive_json())["success"]


async def test_switching_bedtime_on_takes_over_from_a_manual_change(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    comfort = entry.runtime_data.comfort
    await turn_on(hass, BUTLER)
    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.demo_ducted_unit", "hvac_mode": "fan_only"},
        blocking=True,
    )
    assert comfort.autopilot_status("demo_unit")["paused_until"] is not None
    hold = next(e for e in comfort.journal("demo_unit").entries if e.kind.value == "hold")
    assert "in Home Assistant" in hold.reason
    assert hass.states.get(ACTIVITY).state == "paused"
    await turn_on(hass, "switch.demo_ducted_unit_bedtime")
    assert comfort.autopilot_status("demo_unit")["paused_until"] is None


async def test_the_solar_shifting_switch_is_gone(hass: HomeAssistant) -> None:
    registry = er.async_get(hass)
    registry.async_get_or_create("switch", DOMAIN, "demo_unit_solar_shift")
    await setup_demo(hass)
    assert registry.async_get_entity_id("switch", DOMAIN, "demo_unit_solar_shift") is None
