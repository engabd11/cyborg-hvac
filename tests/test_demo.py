from cyborg_hvac.backends.demo.backend import UNIT_ID, DemoBackend
from cyborg_hvac.backends.demo.simulation import SimSystem
from cyborg_hvac.core.model import Command, HvacMode, ZoneCommand


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


async def test_heating_warms_open_zones_only():
    clock = Clock()
    backend = DemoBackend(zone_count=3, clock=clock)
    await backend.async_send(
        UNIT_ID,
        Command(power=True, mode=HvacMode.HEAT, target_temp=24, zones={3: ZoneCommand(on=False)}),
    )
    before = (await backend.async_fetch())[UNIT_ID]
    clock.now += 30 * 60
    after = (await backend.async_fetch())[UNIT_ID]
    assert after.zones[1].temperature > before.zones[1].temperature
    assert after.zones[3].temperature < before.zones[3].temperature


def test_return_air_is_opening_weighted():
    system = SimSystem.create(2)
    system.zones[1].temperature = 20
    system.zones[2].temperature = 24
    system.zones[1].opening = 100
    system.zones[2].opening = 0
    assert system.return_air() == 20
    system.zones[2].opening = 100
    assert system.return_air() == 22


def test_off_unit_drifts_to_outdoor():
    system = SimSystem.create(1, start=20, outdoor=10)
    system.step(60)
    assert system.zones[1].temperature < 20
