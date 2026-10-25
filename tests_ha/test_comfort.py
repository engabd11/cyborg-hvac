"""Smart comfort on the demo unit: options, advice, apply, guest mode, websocket."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.cyborg_hvac.const import DOMAIN
from custom_components.cyborg_hvac.core.model import HvacMode

from .test_demo import setup_demo

WARM_TARGETS = {
    "heat_day": 23.0,
    "heat_night": 23.0,
    "heat_away": 16.0,
    "cool_day": 26.0,
    "cool_night": 26.0,
    "cool_away": 28.0,
    "humidity_max": 65,
    "night_start": "22:00:00",
    "night_end": "06:30:00",
}


async def set_comfort(hass: HomeAssistant, entry, values=WARM_TARGETS):
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "comfort"}
    )
    result = await hass.config_entries.options.async_configure(result["flow_id"], values)
    await hass.async_block_till_done()
    return result


async def test_comfort_targets_must_be_ordered(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    result = await set_comfort(hass, entry, {**WARM_TARGETS, "heat_day": 27.0})
    assert result["errors"] == {"base": "band_order"}


async def test_cold_demo_house_gets_heating_advice_and_apply_works(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    result = await set_comfort(hass, entry)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["heat_day"] == 23.0

    state = hass.states.get("sensor.demo_ducted_unit_advice")
    assert int(state.state) >= 1
    recs = state.attributes["recommendations"]
    assert recs[0]["id"] == "mode_heat"
    assert recs[0]["action"]["mode"] == "heat"

    await hass.services.async_call(
        DOMAIN, "apply_recommendation", {"recommendation": "mode_heat"}, blocking=True
    )
    backend = entry.runtime_data.backend
    assert backend.system.power is True
    assert backend.system.mode is HvacMode.HEAT
    ids = [
        r["id"]
        for r in hass.states.get("sensor.demo_ducted_unit_advice").attributes["recommendations"]
    ]
    assert "mode_heat" not in ids

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "apply_recommendation", {"recommendation": "nope"}, blocking=True
        )


async def test_dismiss_hides_advice(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    await set_comfort(hass, entry)
    await hass.services.async_call(
        DOMAIN,
        "dismiss_recommendation",
        {"recommendation": "mode_heat", "minutes": 30},
        blocking=True,
    )
    recs = hass.states.get("sensor.demo_ducted_unit_advice").attributes["recommendations"]
    assert "mode_heat" not in [r["id"] for r in recs]


async def test_zone_settings_and_guest_mode(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    hass.states.async_set("binary_sensor.lounge_motion", "on")
    master = next(s for s in entry.subentries.values() if s.title == "Master")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "zone"),
        context={"source": "reconfigure", "subentry_id": master.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Living",
            "presence_sensors": ["binary_sensor.lounge_motion"],
            "guest_zone": True,
            "priority": "living",
            "min_opening": 10,
        },
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    sub = entry.subentries[master.subentry_id]
    assert sub.data["guest_zone"] is True
    assert sub.data["min_opening"] == 10
    assert hass.states.get("binary_sensor.living_occupied").state == "on"

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.demo_ducted_unit_guest_mode"}, blocking=True
    )
    comfort = entry.runtime_data.comfort
    assert comfort.guest_modes["demo_unit"] is True
    snapshot = comfort.snapshots["demo_unit"]
    assert snapshot.guest_mode
    assert next(z for z in snapshot.zones if z.zone == 1).guest


async def test_inputs_options_and_websocket(hass: HomeAssistant, hass_ws_client) -> None:
    entry = await setup_demo(hass)
    hass.states.async_set("sensor.outdoor", "31.5", {"unit_of_measurement": "°C"})
    hass.states.async_set("sensor.grid", "-2.4", {"unit_of_measurement": "kW"})
    hass.states.async_set("person.abdullah", "home")
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "inputs"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "outdoor_sensor": "sensor.outdoor",
            "solar_grid_sensor": "sensor.grid",
            "solar_export_negative": True,
            "people": ["person.abdullah"],
            "scan_interval": 30,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{DOMAIN}/list"})
    listing = await client.receive_json()
    assert listing["result"][0]["units"][0]["unit_id"] == "demo_unit"

    await client.send_json({"id": 2, "type": f"{DOMAIN}/subscribe"})
    assert (await client.receive_json())["success"]
    event = (await client.receive_json())["event"]
    assert event["schema"] == 1
    assert event["unit"]["climate"] == "climate.demo_ducted_unit"
    assert event["unit"]["entities"]["guest_mode"] == "switch.demo_ducted_unit_guest_mode"
    assert event["unit"]["features_entities"]["powerful"] == "switch.demo_ducted_unit_powerful"
    assert event["unit"]["features_entities"]["sleep"] == "select.demo_ducted_unit_sleep"
    assert event["unit"]["entities"]["room_balancing"] == (
        "switch.demo_ducted_unit_autopilot_room_balancing"
    )
    assert event["house"]["outdoor"] == 31.5
    assert event["house"]["solar_export_w"] == 2400
    assert event["house"]["anyone_home"] is True
    assert len(event["zones"]) == 3
    assert event["zones"][0]["entities"]["damper"] == "cover.master"
    assert isinstance(event["advice"], list)

    # A change pushes a fresh snapshot.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.demo_ducted_unit_guest_mode"}, blocking=True
    )
    for _ in range(5):
        event = (await client.receive_json())["event"]
        if event["house"]["guest_mode"]:
            break
    assert event["house"]["guest_mode"] is True


async def test_unload_stops_comfort(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "apply_recommendation")
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU


async def test_forecast_drives_the_plan(hass: HomeAssistant) -> None:
    from homeassistant.core import ServiceResponse, SupportsResponse
    from homeassistant.util import dt as dt_util

    start = dt_util.now().replace(minute=0, second=0, microsecond=0)

    async def get_forecasts(call) -> ServiceResponse:
        return {
            call.data["entity_id"]: {
                "forecast": [
                    {"datetime": (start + timedelta(hours=h)).isoformat(), "temperature": 2.0}
                    for h in range(30)
                ]
            }
        }

    hass.services.async_register(
        "weather", "get_forecasts", get_forecasts, supports_response=SupportsResponse.ONLY
    )
    hass.states.async_set("weather.bom", "cloudy", {"temperature": 3.0})
    entry = await setup_demo(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "inputs"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"weather_entity": "weather.bom", "scan_interval": 30}
    )
    await hass.async_block_till_done()
    comfort = entry.runtime_data.comfort
    payload = comfort.as_dict("demo_unit")
    assert payload["forecast_hours"] >= 24
    assert payload["house"]["outdoor"] == 3.0
    kinds = {b["kind"] for b in payload["plan"]["blocks"]}
    assert "preheat" in kinds  # a freezing night ahead of a cool house
    assert payload["plan"]["periods"]
    assert hass.states.get("switch.demo_ducted_unit_autopilot_weather_pre_conditioning")
