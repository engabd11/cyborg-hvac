from dataclasses import replace
from datetime import UTC, datetime

from cyborg_hvac.backends.demo.backend import demo_capabilities
from cyborg_hvac.core.model import Command, HvacMode, UnitState, ZoneCommand
from cyborg_hvac.logic.advisor import evaluate
from cyborg_hvac.logic.autopilot import AutopilotMemory, Switches, decide
from cyborg_hvac.logic.comfort import needs, zone_band
from cyborg_hvac.logic.openings import OpeningMemory, openings_command
from cyborg_hvac.logic.snapshot import HouseSnapshot, ZoneInputs
from cyborg_hvac.logic.thermal import ThermalModel

NIGHT = datetime(2026, 7, 10, 22, 30, tzinfo=UTC)


def snap(zones, *, power=True, mode=HvacMode.HEAT, target=21.0, current=20.0, **kw):
    unit = UnitState(power=power, mode=mode, target_temp=target, current_temp=current)
    return HouseSnapshot(
        now=NIGHT, unit_name="U", caps=demo_capabilities(), unit=unit, zones=tuple(zones), **kw
    )


def z(n, temp=21.0, on=True, opening=100, **kw):
    return ZoneInputs(zone=n, name=f"Z{n}", on=on, opening=opening, temperature=temp, **kw)


def bedtime(s):
    """Return what Bedtime does with this snapshot."""
    return decide(s, Switches(sleep=True), [], ThermalModel(), AutopilotMemory()).command


# Sleep ------------------------------------------------------------------------


def test_sleep_bands_and_focus():
    zones = (z(1, 21.0, sleep=True, occupied=True), z(2, 26.0), z(3, 17.0))
    s = snap(zones, sleep_mode=True, sleep_target=23.0)
    band = zone_band(s, zones[0])
    assert (band.low, band.high) == (22.5, 23.5)
    # Only the bedroom decides what the house needs.
    assert needs(s) == (True, False)
    # The other rooms just need to stay tolerable.
    assert zone_band(s, zones[1]).high >= 28.0


def test_sleep_opens_bedroom_and_closes_the_rest_down_to_the_floor():
    zones = (
        z(1, 21.0, sleep=True, opening=40),
        z(2, 22.0),
        z(3, 22.0, spill=True),
        z(4, 22.0),
    )
    command = bedtime(snap(zones, sleep_mode=True, sleep_target=23.0))
    assert command.zones[1] == ZoneCommand(on=None, opening=100)
    # The spill zone stays as the second air path, as it is: no write needed.
    assert 3 not in command.zones
    assert command.zones[2] == ZoneCommand(on=False)
    assert command.zones[4] == ZoneCommand(on=False)
    # Openings go out before closings, so the fan never sees too little duct.
    assert next(iter(command.zones)) == 1
    assert command.target_temp is not None
    assert command.target_temp >= 23.0  # return air bias and shortfall push it up


def test_sleep_starts_an_idle_unit_in_the_right_mode():
    s = snap((z(1, 25.0, sleep=True), z(2)), power=False, sleep_mode=True, sleep_target=23.0)
    command = bedtime(s)
    assert command.power is True
    assert command.mode is HvacMode.COOL


def test_sleep_lets_a_warm_bedroom_cool_on_its_own_only_when_that_is_soon():
    s = snap(
        (z(1, 24.0, sleep=True), z(2)),
        power=False,
        sleep_mode=True,
        sleep_target=23.0,
        outdoor_temp=6.0,
    )
    assert bedtime(s) == Command()  # outside has it back within half an hour; the unit stays off
    # Two degrees out would take outside an hour: Bedtime does not wait that long.
    far = replace(s, zones=(z(1, 25.0, sleep=True), z(2)))
    command = bedtime(far)
    assert command.power is True
    assert command.mode is HvacMode.COOL


# Openings -----------------------------------------------------------------------


def test_open_door_closes_zone_then_restores_it():
    memory = OpeningMemory()
    s = snap((z(1), z(2), z(3, window_open=True, door_open=True, opening=70)))
    command = openings_command(s, memory)
    assert command.zones == {3: ZoneCommand(on=False)}
    shut = snap((z(1), z(2), z(3, on=False, opening=70)))
    assert openings_command(shut, memory).zones == {3: ZoneCommand(on=True, opening=70)}


def test_everything_open_pauses_the_unit_until_something_shuts():
    memory = OpeningMemory()
    all_open = snap((z(1, window_open=True), z(2, window_open=True)))
    assert openings_command(all_open, memory).power is False
    one_shut = snap((z(1, window_open=False), z(2, window_open=True)), power=False)
    assert openings_command(one_shut, memory).power is True


def test_sleep_zone_window_is_left_to_the_sleeper():
    memory = OpeningMemory()
    s = snap((z(1, sleep=True, window_open=True), z(2)), sleep_mode=True)
    assert openings_command(s, memory) == Command()


def test_door_advice_wording():
    s = snap((z(1), z(2), z(3, window_open=True, door_open=True)), outdoor_temp=10.0)
    rec = next(r for r in evaluate(s) if r.id == "zone_window_3")
    assert "door" in rec.title


def test_guest_focus_steps_aside_in_sleep_mode():
    zones = (z(1, guest=True, opening=50), z(2), z(3), z(4, sleep=True))
    s = snap(zones, outdoor_temp=10.0, guest_mode=True, sleep_mode=True)
    assert "guest_focus" not in [r.id for r in evaluate(s)]
    assert "guest_focus" in [r.id for r in evaluate(replace(s, sleep_mode=False))]


def test_bedtime_never_heats_a_bedroom_that_is_already_warm():
    # The reported case: bedtime at 20.5, bedroom 21.5, return air warmer still
    # because it had been measuring the whole house.
    zones = (z(1, 21.5, sleep=True, occupied=True), z(2, 22.0, spill=True), z(3, 23.5))
    s = snap(zones, target=22.0, current=23.0, sleep_mode=True, sleep_target=20.5)
    command = bedtime(s)
    assert command.mode is None  # one degree warm is not worth switching to cooling
    assert command.power is False or (
        command.target_temp is not None and command.target_temp <= 20.5
    )  # off, or at or below bedtime so the unit idles


def test_bedtime_still_corrects_for_return_air_when_the_room_is_short():
    zones = (z(1, 19.5, sleep=True, occupied=True), z(2, 21.0, spill=True))
    s = snap(zones, target=20.5, current=21.0, sleep_mode=True, sleep_target=20.5)
    command = bedtime(s)
    assert command.target_temp > 20.5


def test_bedtime_never_cools_a_bedroom_that_is_already_cool():
    zones = (z(1, 22.0, sleep=True, occupied=True), z(2, 24.0, spill=True))
    s = snap(
        zones, mode=HvacMode.COOL, target=23.0, current=21.5, sleep_mode=True, sleep_target=23.0
    )
    command = bedtime(s)
    assert command.mode is None
    # Off, or a cooling setpoint at or above bedtime (23 already is: it idles).
    assert command.power is False or (command.target_temp or s.unit.target_temp) >= 23.0
