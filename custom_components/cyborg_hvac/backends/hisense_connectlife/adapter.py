"""Home Assistant glue for the Hisense ConnectLife backend.

This is the only module of the brand that imports Home Assistant. It provides
the OAuth implementation used by the config flow and builds the backend from a
config entry.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    OAuth2TokenRequestError,
    OAuth2TokenRequestReauthError,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import CONF_SOURCE_ID, CONF_TOKEN, DOMAIN
from ...core.errors import AuthFailed, CannotConnect
from .backend import HisenseConnectLifeBackend
from .client import (
    CLIENT_ID,
    CLIENT_SECRET,
    OAUTH_AUTHORIZE_URL,
    OAUTH_TOKEN_URL,
    ConnectLifeClient,
    new_source_id,
)

# ConnectLife only accepts this redirect for Hisense's Home Assistant client.
DEFAULT_REDIRECT_URI = "http://homeassistant.local:8123/auth/external/callback"
IMPLEMENTATION_DOMAIN = "cyborg_hvac_connectlife"
DEFAULT_EXPIRES_IN = 3600


class ConnectLifeOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """Sign in with a ConnectLife account (any login the app supports)."""

    def __init__(self, hass: HomeAssistant, redirect_uri: str = DEFAULT_REDIRECT_URI) -> None:
        """Use Hisense's published client with a fixed redirect."""
        super().__init__(
            hass,
            IMPLEMENTATION_DOMAIN,
            CLIENT_ID,
            CLIENT_SECRET,
            OAUTH_AUTHORIZE_URL,
            OAUTH_TOKEN_URL,
        )
        self._redirect_uri = redirect_uri

    @property
    def name(self) -> str:
        """Return the name shown when picking a sign-in method."""
        return "ConnectLife"

    @property
    def service_domain(self) -> str:
        """Report token errors against this integration."""
        return DOMAIN

    @property
    def redirect_uri(self) -> str:
        """Return the redirect ConnectLife accepts."""
        return self._redirect_uri

    async def _token_request(self, data: dict) -> dict:
        data["redirect_uri"] = self._redirect_uri
        token = await super()._token_request(data)
        return normalise_token(token)

    def paste_authorize_url(self, state: str) -> str:
        """Return a sign-in link for the paste-the-address method."""
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": self._redirect_uri,
                "state": state,
            }
        )
        return f"{self.authorize_url}?{query}"

    async def async_exchange_code(self, code: str) -> dict:
        """Swap an authorisation code for tokens, with expiry filled in."""
        token = await self._token_request({"grant_type": "authorization_code", "code": code})
        token["expires_in"] = int(token["expires_in"])
        token["expires_at"] = time.time() + token["expires_in"]
        return token


def normalise_token(token: dict) -> dict:
    """Make sure a token has an integer ``expires_in``."""
    token = dict(token)
    expires = token.get("expires_in")
    try:
        token["expires_in"] = int(expires)
    except (TypeError, ValueError):
        token["expires_in"] = DEFAULT_EXPIRES_IN
    return token


def extract_code(redirected_url: str, expected_state: str | None) -> str | None:
    """Pull the authorisation code out of the address the browser ended on."""
    text = redirected_url.strip()
    if "code=" not in text:
        return None
    query = parse_qs(urlparse(text).query) if "?" in text else parse_qs(text)
    codes = query.get("code")
    if not codes:
        return None
    states = query.get("state")
    if expected_state is not None and states and states[0] != expected_state:
        return None
    return codes[0]


def account_id_from_token(token: dict[str, Any], unit_ids: list[str]) -> str:
    """Return a stable id for the account behind a token."""
    for key in ("userId", "user_id", "customerId", "uid", "openId", "accountId"):
        if token.get(key):
            return str(token[key])
    access = str(token.get("access_token", ""))
    parts = access.split(".")
    if len(parts) == 3:
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded))
            for key in ("userId", "uid", "sub"):
                if claims.get(key):
                    return str(claims[key])
        except ValueError:
            pass
    joined = ",".join(sorted(unit_ids))
    return "units-" + hashlib.sha256(joined.encode()).hexdigest()[:16]


def async_get_implementation(hass: HomeAssistant) -> ConnectLifeOAuth2Implementation:
    """Register (once) and return the OAuth implementation."""
    implementation = ConnectLifeOAuth2Implementation(hass)
    config_entry_oauth2_flow.async_register_implementation(hass, DOMAIN, implementation)
    return implementation


async def async_create_backend(
    hass: HomeAssistant, entry: ConfigEntry
) -> HisenseConnectLifeBackend:
    """Build the backend for a config entry, with token refresh."""
    implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
        hass, entry
    )
    session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

    async def token_provider() -> str:
        try:
            await session.async_ensure_token_valid()
        except OAuth2TokenRequestReauthError as err:
            raise AuthFailed("ConnectLife sign-in expired") from err
        except (OAuth2TokenRequestError, ClientError) as err:
            raise CannotConnect(f"Could not refresh the ConnectLife token: {err}") from err
        return session.token["access_token"]

    async def on_token_rejected() -> None:
        # Mark the token expired so the next request refreshes it.
        expired = {**session.token, "expires_at": 0}
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_TOKEN: expired})

    client = ConnectLifeClient(
        async_get_clientsession(hass),
        token_provider,
        source_id=entry.data.get(CONF_SOURCE_ID) or new_source_id(),
        timezone=str(hass.config.time_zone or "UTC"),
        on_token_rejected=on_token_rejected,
    )
    return HisenseConnectLifeBackend(
        client,
        async_get_clientsession(hass),
        task_factory=lambda coro: entry.async_create_background_task(
            hass, coro, f"{DOMAIN} ConnectLife push"
        ),
    )


async def async_create_flow_backend(
    hass: HomeAssistant, data: dict[str, Any]
) -> HisenseConnectLifeBackend:
    """Build a short-lived backend from a fresh token, for the config flow."""
    token = data[CONF_TOKEN]

    async def token_provider() -> str:
        return token["access_token"]

    client = ConnectLifeClient(
        async_get_clientsession(hass),
        token_provider,
        source_id=data.get(CONF_SOURCE_ID) or new_source_id(),
        timezone=str(hass.config.time_zone or "UTC"),
    )
    return HisenseConnectLifeBackend(client, async_get_clientsession(hass))
