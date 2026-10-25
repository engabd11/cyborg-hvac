"""Set up the demo brand end to end and drive its entities."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
import pytest

from custom_components.cyborg_hvac.const import DOMAIN
from custom_components.cyborg_hvac.core.model import Feature, HvacMode
from custom_components.cyborg_hvac.diagnostics import async_get_config_entry_diagnostics


async def setup_demo(hass: HomeAssistant, names=("Master", "Office", "Living")):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "demo"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"demo_zones": 5})
    assert result["step_id"] == "zones"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"zone_count": len(names)}
    )
    assert result["step_id"] == "zone_names"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {f"zone_{i}": name for i, name in enumerate(names, start=1)}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    return result["result"]


async def test_demo_creates_unit_and_zone_entities(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    assert len(entry.subentries) == 3
    assert hass.states.get("climate.demo_ducted_unit") is not None
    for name in ("master", "office", "living"):
        assert hass.states.get(f"cover.{name}") is not None
        assert hass.states.get(f"switch.{name}_power") is not None
        assert hass.states.get(f"sensor.{name}_temperature") is not None
    assert hass.states.get("switch.demo_ducted_unit_powerful") is not None


async def test_damper_position_and_zone_switch(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    backend = entry.runtime_data.backend

    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": "cover.office", "position": 42}, blocking=True
    )
    assert backend.system.zones[2].opening == 40
    assert backend.system.zones[2].on is True
    assert hass.states.get("cover.office").attributes["current_position"] == 40

    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.office_power"}, blocking=True
    )
    assert backend.system.zones[2].on is False
    assert backend.system.zones[2].opening == 40
    assert hass.states.get("cover.office").state == "closed"

    await hass.services.async_call(
        "cover", "open_cover", {"entity_id": "cover.office"}, blocking=True
    )
    assert backend.system.zones[2].on is True
    assert backend.system.zones[2].opening == 40


async def test_writes_in_one_window_are_batched(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    backend = entry.runtime_data.backend
    sent = []
    original = backend.async_send

    async def recording_send(unit_id, command):
        sent.append(command)
        await original(unit_id, command)

    backend.async_send = recording_send
    await hass.services.async_call(
        "cover",
        "set_cover_position",
        {"entity_id": ["cover.master", "cover.office", "cover.living"], "position": 60},
        blocking=True,
    )
    assert len(sent) == 1
    assert set(sent[0].zones) == {1, 2, 3}


async def test_climate_modes_and_powerful_rules(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    backend = entry.runtime_data.backend

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            "switch", "turn_on", {"entity_id": "switch.demo_ducted_unit_powerful"}, blocking=True
        )

    await hass.services.async_call(
        "climate",
        "set_temperature",
        {"entity_id": "climate.demo_ducted_unit", "temperature": 22.5, "hvac_mode": "heat"},
        blocking=True,
    )
    assert backend.system.power is True
    assert backend.system.mode is HvacMode.HEAT
    assert backend.system.target == 22.5

    await hass.services.async_call(
        "climate",
        "set_preset_mode",
        {"entity_id": "climate.demo_ducted_unit", "preset_mode": "boost"},
        blocking=True,
    )
    assert backend.system.features[Feature.POWERFUL] is True
    assert hass.states.get("climate.demo_ducted_unit").attributes["preset_mode"] == "boost"

    await hass.services.async_call(
        "climate",
        "set_hvac_mode",
        {"entity_id": "climate.demo_ducted_unit", "hvac_mode": "off"},
        blocking=True,
    )
    assert backend.system.power is False
    assert hass.states.get("climate.demo_ducted_unit").state == "off"


async def test_failed_write_reports_error_and_reverts(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    backend = entry.runtime_data.backend
    backend.fail_next_send = True
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            "cover", "close_cover", {"entity_id": "cover.master"}, blocking=True
        )
    await hass.async_block_till_done()
    assert backend.system.zones[1].on is True
    assert hass.states.get("cover.master").state == "open"


async def test_add_rename_and_remove_zone(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "zone"), context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"slot": "demo_unit|4", "name": "Guest"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert hass.states.get("cover.guest") is not None

    guest = next(s for s in entry.subentries.values() if s.title == "Guest")
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "zone"),
        context={"source": "reconfigure", "subentry_id": guest.subentry_id},
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {"name": "Guest room"}
    )
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert entry.subentries[guest.subentry_id].title == "Guest room"

    assert hass.config_entries.async_remove_subentry(entry, guest.subentry_id)
    await hass.async_block_till_done()
    assert len(entry.subentries) == 3


async def test_diagnostics_redact_identifiers(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["units"][0]["info"]["unit_id"] == "**REDACTED**"
    assert len(diagnostics["entry"]["zones"]) == 3


async def test_unload(hass: HomeAssistant) -> None:
    entry = await setup_demo(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
