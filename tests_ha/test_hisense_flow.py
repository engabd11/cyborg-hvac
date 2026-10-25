"""ConnectLife sign-in by pasting the redirected address, then zone setup."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.cyborg_hvac.backends.hisense_connectlife.backend import (
    HisenseConnectLifeBackend,
)
from custom_components.cyborg_hvac.brands import BRANDS, BrandAdapter
from custom_components.cyborg_hvac.const import BRAND_HISENSE, DOMAIN
from custom_components.cyborg_hvac.core.errors import CannotConnect

TOKEN = {"access_token": "a", "refresh_token": "r", "expires_in": 3600, "expires_at": 9e12}


class FakeClient:
    def __init__(self, fixture):
        self.fixture = fixture
        self.device = json.loads(json.dumps(fixture["device"]))
        self.writes = []

    async def async_get_devices(self):
        return [json.loads(json.dumps(self.device))]

    async def async_get_property_list(self, type_code, feature_code):
        return self.fixture["properties"]

    async def async_query_static_data(self, puid):
        return self.fixture["static_data"]

    async def async_set_properties(self, puid, props):
        self.writes.append(props)
        self.device["statusList"].update(props)
        return props

    async def async_register_phone(self, phone_code):
        raise CannotConnect("push unavailable in tests")


async def test_paste_sign_in_and_zone_setup(hass: HomeAssistant, hacz01) -> None:
    client = FakeClient(hacz01)

    async def fake_backend(hass, data_or_entry):
        return HisenseConnectLifeBackend(client, session=None)

    fake = BrandAdapter(BRAND_HISENSE, fake_backend, fake_backend)
    with (
        patch.dict(BRANDS, {BRAND_HISENSE: fake}),
        patch(
            "custom_components.cyborg_hvac.backends.hisense_connectlife.adapter."
            "ConnectLifeOAuth2Implementation.async_exchange_code",
            AsyncMock(return_value=dict(TOKEN)),
        ) as exchange,
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
        assert result["type"] is FlowResultType.MENU
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "hisense_connectlife"}
        )
        assert result["step_id"] == "hisense_connectlife"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "hisense_paste"}
        )
        assert result["step_id"] == "hisense_paste"
        url = result["description_placeholders"]["url"]
        query = parse_qs(urlparse(url).query)
        assert query["redirect_uri"] == ["http://homeassistant.local:8123/auth/external/callback"]
        state = query["state"][0]

        bad = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": "http://homeassistant.local:8123/nothing"}
        )
        assert bad["errors"] == {"base": "invalid_redirect_url"}

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "redirect_url": "http://homeassistant.local:8123/auth/external/callback"
                f"?code=abc123&state={state}"
            },
        )
        exchange.assert_awaited_once_with("abc123")
        assert result["step_id"] == "zones"
        schema_default = next(
            key.default() for key in result["data_schema"].schema if key == "zone_count"
        )
        assert schema_default == 5

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"zone_count": 5}
        )
        names = ["MASTER", "Mariams Office", "HQ", "Living Room", "Kitchen & Dining"]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {f"zone_{i}": n for i, n in enumerate(names, start=1)}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        entry = result["result"]
        assert entry.title == "Duct Air Conditioner"
        assert entry.data["token"]["access_token"] == "a"
        assert sorted(s.title for s in entry.subentries.values()) == sorted(names)
        await hass.async_block_till_done()

        assert hass.states.get("climate.duct_air_conditioner") is not None
        assert hass.states.get("cover.mariams_office").attributes["current_position"] == 30
        assert hass.states.get("switch.duct_air_conditioner_health") is not None
        assert hass.states.get("switch.duct_air_conditioner_ai_mode") is None

        await hass.services.async_call(
            "cover",
            "set_cover_position",
            {"entity_id": "cover.hq", "position": 30},
            blocking=True,
        )
        assert client.writes[-1] == {"aus_zone3_power": "1", "aus_zone3_opencontrol": "30"}


async def test_same_account_aborts(hass: HomeAssistant, hacz01) -> None:
    client = FakeClient(hacz01)

    async def fake_backend(hass, data_or_entry):
        return HisenseConnectLifeBackend(client, session=None)

    fake = BrandAdapter(BRAND_HISENSE, fake_backend, fake_backend)
    exchange = AsyncMock(return_value=dict(TOKEN, userId="user-1"))
    with (
        patch.dict(BRANDS, {BRAND_HISENSE: fake}),
        patch(
            "custom_components.cyborg_hvac.backends.hisense_connectlife.adapter."
            "ConnectLifeOAuth2Implementation.async_exchange_code",
            exchange,
        ),
    ):
        for attempt in range(2):
            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_USER}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "hisense_connectlife"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"next_step_id": "hisense_paste"}
            )
            state = parse_qs(urlparse(result["description_placeholders"]["url"]).query)["state"][0]
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], {"redirect_url": f"?code=x&state={state}"}
            )
            if attempt == 0:
                result = await hass.config_entries.flow.async_configure(
                    result["flow_id"], {"zone_count": 0}
                )
                assert result["type"] is FlowResultType.CREATE_ENTRY
                await hass.async_block_till_done()
            else:
                assert result["type"] is FlowResultType.ABORT
                assert result["reason"] == "already_configured"
