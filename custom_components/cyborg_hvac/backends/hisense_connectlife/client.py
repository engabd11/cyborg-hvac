"""Signed REST client for the ConnectLife cloud (Hisense).

The request signing and system parameters follow Hisense's own Home Assistant
plugin (MIT licence, Copyright (c) 2025 Hisense), see NOTICE. Responses are
checked for ``resultCode`` and mapped onto the core error types.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import hashlib
import hmac
import json
import logging
import time
from typing import Any
import uuid

import aiohttp

from ...core.errors import (
    AccountActionRequired,
    AuthFailed,
    CannotConnect,
    CommandRejected,
    RateLimited,
)

_LOGGER = logging.getLogger(__name__)

# Hisense's published OAuth client for Home Assistant (from their MIT plugin).
CLIENT_ID = "9793620883275788"
CLIENT_SECRET = "7h1m3gZVlILyBvIFBNmzXwoFYLhkGqG9NQd2jBzuZCqJKCTyCtYwQtXi4tVBjg9B"  # noqa: S105
OAUTH_AUTHORIZE_URL = "https://oauth.hijuconn.com/login"
OAUTH_TOKEN_URL = "https://oauth.hijuconn.com/oauth/token"  # noqa: S105
API_BASE_URL = "https://juapi-3rd.hijuconn.com"

PATH_DEVICE_LIST = "/clife-svc/pu/get_device_status_list"
PATH_PROPERTY_LIST = "/clife-svc/get_property_list"
PATH_STATIC_DATA = "/clife-svc/pu/query_static_data"
PATH_SET_PROPERTIES = "/device/pu/property/set"
PATH_SELF_CHECK = "/basic/self_check/info"
PATH_HOUR_POWER = "/clife-svc/pu/get_hour_power"
PATH_REGISTER_PHONE = "/msg/registerPhoneDevice"
PATH_PUSH_CHANNELS = "/msg/get_msg_and_channels"

SIGN_HEADER = "hi-params-encrypt"
EMPTY_BODY_DIGEST = "47DEQpj8HBSa+/TImW+5JCeuQeRkm5NMpJWZG3hSuFU="

# resultCode values with a known meaning.
CODE_INVALID_TOKEN = {100026, 100027, 100028}
CODE_RANDSTR_CHECK_FAILED = 101005
CODE_COMMAND_MUTEX = {16, 160001}
CODE_PENDING_REGISTRATION = 206001

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)

TokenProvider = Callable[[], Awaitable[str]]
TokenInvalidator = Callable[[], Awaitable[None]]


def gmt_date(now: datetime | None = None) -> str:
    """Return the HTTP date used in the signature."""
    return (now or datetime.now(UTC)).strftime("%a, %d %b %Y %H:%M:%S GMT")


def body_digest(body_text: str | None) -> str:
    """Return the base64 SHA-256 digest of the request body."""
    if not body_text:
        return EMPTY_BODY_DIGEST
    return base64.b64encode(hashlib.sha256(body_text.encode()).digest()).decode()


def signing_string(app_id: str, method: str, path: str, date: str) -> str:
    """Return the string that is signed with the app secret."""
    return f"{app_id}\n{method} {path}\ndate: {date}\n{SIGN_HEADER}: {app_id}\n"


def sign(secret: str, value: str) -> str:
    """Return the base64 HMAC-SHA256 signature."""
    digest = hmac.new(secret.encode(), value.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def signed_headers(
    app_id: str, app_secret: str, method: str, path: str, body_text: str | None, date: str
) -> dict[str, str]:
    """Return the headers that authenticate one request."""
    signature = sign(app_secret, signing_string(app_id, method, path, date))
    return {
        SIGN_HEADER: app_id,
        "Date": date,
        "Authorization": (
            f'Signature signature="{signature}", keyId="{app_id}",'
            f'algorithm="hmac-sha256", headers="@request-target date {SIGN_HEADER}"'
        ),
        "Content-Type": "application/json",
        "Digest": f"SHA-256={body_digest(body_text)}",
    }


def new_source_id() -> str:
    """Return a new client source id. Keep it stable per installation."""
    seed = f"{uuid.uuid1().hex}{int(time.time() * 1000)}"
    return f"td001002000{hashlib.md5(seed.encode()).hexdigest()}"  # noqa: S324


def _query_string(params: dict[str, Any]) -> str:
    # Built by hand, as in Hisense's client: the signature covers the path and
    # query exactly as sent, so the encoding must stay byte-for-byte the same.
    parts = []
    for key, value in params.items():
        if isinstance(value, (dict, list)):
            value = json.dumps(value)
        parts.append(f"{key}={value}")
    return "&".join(parts)


class _TokenRejected(Exception):
    """Internal: the access token was refused."""


class _RetryableNonce(Exception):
    """Internal: the server rejected the random nonce; retry with a new one."""


class ConnectLifeClient:
    """Minimal async client for the ConnectLife third-party API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token_provider: TokenProvider,
        *,
        source_id: str,
        timezone: str = "UTC",
        on_token_rejected: TokenInvalidator | None = None,
        base_url: str = API_BASE_URL,
        app_id: str = CLIENT_ID,
        app_secret: str = CLIENT_SECRET,
    ) -> None:
        """Create a client. ``token_provider`` returns a valid access token."""
        self._session = session
        self._token_provider = token_provider
        self._on_token_rejected = on_token_rejected
        self._source_id = source_id
        self._timezone = timezone
        self._base_url = base_url
        self._app_id = app_id
        self._app_secret = app_secret

    async def async_request(
        self, method: str, path: str, data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Send a signed request and return the decoded response."""
        attempts = 0
        while True:
            attempts += 1
            try:
                return await self._request_once(method, path, data)
            except _RetryableNonce:
                if attempts >= 3:
                    raise CannotConnect("ConnectLife rejected the request nonce") from None
            except _TokenRejected:
                if attempts >= 2 or self._on_token_rejected is None:
                    raise AuthFailed("ConnectLife rejected the access token") from None
                await self._on_token_rejected()

    async def _request_once(
        self, method: str, path: str, data: dict[str, Any] | None
    ) -> dict[str, Any]:
        method = method.upper()
        token = await self._token_provider()
        timestamp = int(time.time() * 1000)
        params: dict[str, Any] = dict(data or {})
        params.update(
            {
                "timeStamp": str(timestamp),
                "version": "8.1",
                "languageId": "1",
                "timezone": self._timezone,
                "randStr": hashlib.md5(  # noqa: S324
                    f"{uuid.uuid1()}{timestamp}".encode()
                ).hexdigest(),
                "appId": self._app_id,
                "sourceId": self._source_id,
                "platformId": 5,
            }
        )
        headers: dict[str, str] = {}
        body_text: str | None = None
        path_with_query = path
        if method == "GET":
            headers["accessToken"] = token
            path_with_query = f"{path}?{_query_string(params)}"
        else:
            params["accessToken"] = token
            body_text = json.dumps(params)
        headers.update(
            signed_headers(
                self._app_id, self._app_secret, method, path_with_query, body_text, gmt_date()
            )
        )

        url = f"{self._base_url}{path_with_query}"
        try:
            async with self._session.request(
                method, url, data=body_text, headers=headers, timeout=REQUEST_TIMEOUT
            ) as resp:
                text = await resp.text()
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise CannotConnect(f"ConnectLife request failed: {err}") from err

        if status == 401:
            raise _TokenRejected
        if status == 429:
            raise RateLimited("ConnectLife rate limit reached")
        if status >= 400:
            raise CannotConnect(f"ConnectLife replied HTTP {status} for {path}")

        try:
            payload = json.loads(text)
        except ValueError as err:
            raise CannotConnect(f"ConnectLife sent invalid JSON for {path}") from err
        if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
            payload = payload["response"]
        if not isinstance(payload, dict):
            raise CannotConnect(f"ConnectLife sent an unexpected reply for {path}")

        code = payload.get("resultCode")
        try:
            code_int = int(code)
        except (TypeError, ValueError):
            code_int = -1
        if code_int == 0:
            return payload

        message = str(payload.get("msg") or payload.get("message") or "")
        _LOGGER.debug("ConnectLife %s %s failed: %s %s", method, path, code, message)
        if code_int in CODE_INVALID_TOKEN:
            raise _TokenRejected
        if code_int == CODE_RANDSTR_CHECK_FAILED:
            raise _RetryableNonce
        if code_int in CODE_COMMAND_MUTEX:
            raise CommandRejected("device_busy", message or "The unit refused the command")
        if code_int == CODE_PENDING_REGISTRATION:
            raise AccountActionRequired("accept_terms", message)
        raise CannotConnect(f"ConnectLife error {code}: {message}")

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return every device on the account with its current status."""
        payload = await self.async_request("GET", PATH_DEVICE_LIST)
        return list(payload.get("deviceList") or [])

    async def async_get_property_list(
        self, type_code: str, feature_code: str
    ) -> list[dict[str, Any]]:
        """Return the property metadata for a device model."""
        payload = await self.async_request(
            "GET",
            PATH_PROPERTY_LIST,
            {"deviceTypeCode": type_code, "deviceFeatureCode": feature_code},
        )
        return list(payload.get("properties") or [])

    async def async_query_static_data(self, puid: str) -> dict[str, Any]:
        """Return the static capability flags of a device."""
        payload = await self.async_request("POST", PATH_STATIC_DATA, {"puid": puid})
        return dict(payload.get("data") or {})

    async def async_set_properties(self, puid: str, properties: dict[str, str]) -> dict[str, Any]:
        """Write properties to a device. Every value is sent as a string."""
        payload = await self.async_request(
            "POST", PATH_SET_PROPERTIES, {"puid": puid, "properties": properties}
        )
        return dict(payload.get("kvMap") or {})

    async def async_self_check(self, puid: str) -> list[str]:
        """Return the status keys of active faults."""
        payload = await self.async_request("POST", PATH_SELF_CHECK, {"noRecord": "1", "puid": puid})
        failed = (payload.get("data") or {}).get("selfCheckFailedList") or []
        return [item.get("statusKey") for item in failed if item.get("statusKey")]

    async def async_register_phone(self, phone_code: str) -> None:
        """Register a push client id."""
        await self.async_request("POST", PATH_REGISTER_PHONE, {"phoneCode": phone_code})

    async def async_get_push_channels(self, phone_code: str) -> dict[str, Any]:
        """Return the push server address and channels for a client id."""
        return await self.async_request(
            "GET",
            PATH_PUSH_CHANNELS,
            {"pageNo": "1", "pageSize": "10", "phoneCode": phone_code, "queryType": 2},
        )

    async def async_access_token(self) -> str:
        """Return a valid access token (used by the push channel)."""
        return await self._token_provider()
