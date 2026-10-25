"""Sleep mode, door delays, presence hold and spill zones on the demo unit."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cyborg_hvac.core.model import HvacMode

from .test_demo import setup_demo


async def configure_zone(hass, entry, title, **settings):
    sub = next(s for s in entry.subentries.values() if s.title == title)
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "zone"),
        context={"source": "reconfigure", "subentry_id": sub.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": title, **settings}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()


async def test_presence_hold_keeps_a_quiet_room_occupied(hass: HomeAssistant, freezer) -> None:
    entry = await setup_demo(hass)
    hass.states.async_set("binary_sensor.bed_motion", "on")
    await configure_zone(
        hass, entry, "Master", presence_sensors=["binary_sensor.bed_motion"], presence_hold=15
    )
    comfort = entry.runtime_data.comfort
    hass.states.async_set("binary_sensor.bed_motion", "off")
    freezer.tick(timedelta(minutes=10))
    await comfort.async_refresh()
    master = next(z for z in comfort.snapshots["demo_unit"].zones if z.zone == 1)
    assert master.occupied is True  # quiet for 10 min, still in bed
    freezer.tick(timedelta(minutes=10))
    await comfort.async_refresh()
    master = next(z for z in comfort.snapshots["demo_unit"].zones if z.zone == 1)
    assert master.occupied is False
    assert master.vacant_minutes >= 19


async def test_door_counts_as_open_only_after_its_delay(hass: HomeAssistant, freezer) -> None:
    entry = await setup_demo(hass)
    hass.states.async_set("binary_sensor.sliding_door", "off", {"device_class": "door"})
    await configure_zone(hass, entry, "Living", door_sensors=["binary_sensor.sliding_door"])
    comfort = entry.runtime_data.comfort
    hass.states.async_set("binary_sensor.sliding_door", "on", {"device_class": "door"})
    freezer.tick(timedelta(minutes=2))
    await comfort.async_refresh()
    living = next(z for z in comfort.snapshots["demo_unit"].zones if z.zone == 3)
    assert not living.window_open  # walked through, not left open
    freezer.tick(timedelta(minutes=4))
    await comfort.async_refresh()
    living = next(z for z in comfort.snapshots["demo_unit"].zones if z.zone == 3)
    assert living.window_open
    assert living.door_open


async def test_sleep_mode_holds_the_bedroom(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass, names=("Master", "Office", "Living", "Dining"))
    await configure_zone(hass, entry, "Master", sleep_zone=True)
    await configure_zone(hass, entry, "Dining", spill=True)
    comfort = entry.runtime_data.comfort
    system = entry.runtime_data.backend.system
    system.power, system.mode = False, HvacMode.HEAT
    system.zones[1].temperature = 19.0

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.demo_ducted_unit_sleep_temperature", "value": 23},
        blocking=True,
    )
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.demo_ducted_unit_autopilot_bedtime"},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert comfort.sleep_targets["demo_unit"] == 23
    assert system.power is True
    assert system.mode is HvacMode.HEAT
    assert system.zones[1].on is True
    assert system.zones[1].opening == 100
    # The spill zone stays as the second air path; the others close.
    assert system.zones[4].on is True
    assert system.zones[2].on is False
    assert system.zones[3].on is False
    payload = comfort.as_dict("demo_unit")
    assert payload["house"]["sleep_mode"] is True
    assert payload["house"]["sleep_target"] == 23
    assert payload["zones"][0]["sleep"] is True
    assert (
        payload["unit"]["entities"]["sleep_target"] == "number.demo_ducted_unit_sleep_temperature"
    )


async def test_controller_opening_the_spill_zone_is_not_a_manual_change(
    hass: HomeAssistant,
) -> None:
    entry = await setup_demo(hass, names=("Master", "Office", "Living", "Dining"))
    await configure_zone(hass, entry, "Dining", spill=True)
    comfort = entry.runtime_data.comfort
    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.demo_ducted_unit_autopilot_room_balancing"},
        blocking=True,
    )
    system = entry.runtime_data.backend.system
    system.zones[4].on = False
    await entry.runtime_data.coordinator.async_refresh()
    system.zones[4].on = True  # the controller spills air into dining
    await entry.runtime_data.coordinator.async_refresh()
    await hass.async_block_till_done()
    assert "4" not in comfort.autopilot_status("demo_unit")["zones_paused"]
