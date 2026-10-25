import base64
from dataclasses import replace
import json

from cyborg_hvac.backends.hisense_connectlife.backend import HisenseConnectLifeBackend
from cyborg_hvac.backends.hisense_connectlife.push import (
    PushMessage,
    decode_frame,
)
from cyborg_hvac.core.errors import CommandRejected
from cyborg_hvac.core.model import Command, Feature, HvacMode, ZoneCommand
import pytest


class FakeClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.device = json.loads(json.dumps(fixture["device"]))
        self.writes = []

    async def async_get_devices(self):
        return [json.loads(json.dumps(self.device)), {"deviceTypeCode": "007", "puid": "dehum"}]

    async def async_get_property_list(self, type_code, feature_code):
        return self.fixture["properties"]

    async def async_query_static_data(self, puid):
        return self.fixture["static_data"]

    async def async_set_properties(self, puid, props):
        self.writes.append((puid, props))
        self.device["statusList"].update(props)
        return props


@pytest.fixture
def backend(hacz01):
    client = FakeClient(hacz01)
    return HisenseConnectLifeBackend(client, session=None), client


async def test_discover_only_supported_types(backend):
    backend, _ = backend
    units = await backend.async_discover()
    assert len(units) == 1
    unit = units[0]
    assert unit.unit_id == "puid-hacz01"
    assert unit.name == "Duct Air Conditioner"
    assert unit.model == "HACZ01 ducted zone controller"
    assert unit.model_id == "009-19901"
    assert unit.installed_zones_hint == (1, 2, 3, 4, 5)


async def test_fetch_and_send(backend):
    backend, client = backend
    await backend.async_discover()
    states = await backend.async_fetch()
    assert states["puid-hacz01"].online
    await backend.async_send(
        "puid-hacz01", Command(power=False, zones={1: ZoneCommand(opening=70)})
    )
    assert client.writes == [("puid-hacz01", {"t_power": "0", "aus_zone1_opencontrol": "70"})]
    states = await backend.async_fetch()
    assert not states["puid-hacz01"].power
    assert states["puid-hacz01"].zones[1].opening == 70


async def test_offline_state(backend):
    backend, client = backend
    await backend.async_discover()
    client.device["offlineState"] = 0
    states = await backend.async_fetch()
    assert not states["puid-hacz01"].online


async def test_validate_powerful_needs_heat_or_cool(backend):
    backend, _ = backend
    units = await backend.async_discover()
    state = replace((await backend.async_fetch())["puid-hacz01"], power=False)
    with pytest.raises(CommandRejected):
        backend.validate(units[0], state, Command(features={Feature.POWERFUL: True}))
    backend.validate(
        units[0], state, Command(power=True, mode=HvacMode.HEAT, features={Feature.POWERFUL: True})
    )
    with pytest.raises(CommandRejected):
        backend.validate(units[0], state, Command(fan_speed="turbo"))


async def test_push_updates_state(backend):
    backend, _ = backend
    await backend.async_discover()
    seen = []
    backend._push_callback = lambda unit_id, state: seen.append((unit_id, state))
    backend._handle_push(PushMessage(puid="puid-hacz01", properties={"aus_zone2_power": "0"}))
    backend._handle_push(PushMessage(puid="puid-hacz01", online=False))
    assert seen[0][1].zones[2].on is False
    assert seen[1][1].online is False


def _frame(payload):
    return base64.b64encode(json.dumps(payload).encode()).decode()


def test_decode_device_status_frame():
    status = base64.b64encode(json.dumps({"t_power": "1"}).encode()).decode()
    frame = _frame(
        {
            "msgTypeCode": "status_devicestatus",
            "content": json.dumps({"puid": "p1", "status": status, "properties": {"t_temp": "22"}}),
        }
    )
    message = decode_frame(frame)
    assert message == PushMessage(puid="p1", properties={"t_power": "1", "t_temp": "22"})


def test_decode_wifi_status_frame():
    frame = _frame(
        {
            "msgTypeCode": "status_wifistatus",
            "content": json.dumps({"puid": "p1", "onlinestats": 0}),
        }
    )
    assert decode_frame(frame) == PushMessage(puid="p1", online=False)


def test_decode_ignores_other_frames():
    assert decode_frame(_frame({"msgTypeCode": "device_to_app_notify", "content": "{}"})) is None
    assert decode_frame("not base64!") is None
