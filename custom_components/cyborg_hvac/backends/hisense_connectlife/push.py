"""ConnectLife push channel.

The registration and frame format follow Hisense's own plugin (MIT licence, see
NOTICE). Differences: it reconnects forever with backoff instead of giving up,
it never drops frames that arrive close together, and it treats
``onlinestats == 1`` as online.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
from collections.abc import Callable, Coroutine
import contextlib
from dataclasses import dataclass, field
import json
import logging
from typing import Any
import uuid

import aiohttp

from ...core.errors import HvacError
from .client import ConnectLifeClient

_LOGGER = logging.getLogger(__name__)

MSG_DEVICE_STATUS = "status_devicestatus"
MSG_WIFI_STATUS = "status_wifistatus"
BACKOFF_START = 5
BACKOFF_MAX = 300

TaskFactory = Callable[[Coroutine[Any, Any, None]], asyncio.Task]


@dataclass(slots=True)
class PushMessage:
    """A decoded push frame for one device."""

    puid: str
    properties: dict[str, Any] = field(default_factory=dict)
    online: bool | None = None


def decode_frame(text: str) -> PushMessage | None:
    """Decode a base64 JSON push frame. Return None for frames we ignore."""
    try:
        data = json.loads(base64.b64decode(text).decode())
    except (binascii.Error, UnicodeDecodeError, ValueError):
        _LOGGER.debug("Ignoring undecodable push frame")
        return None
    if not isinstance(data, dict):
        return None
    msg_type = data.get("msgTypeCode")
    if msg_type not in (MSG_DEVICE_STATUS, MSG_WIFI_STATUS):
        return None
    content = data.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            return None
    if not isinstance(content, dict) or not content.get("puid"):
        return None

    message = PushMessage(puid=str(content["puid"]))
    if msg_type == MSG_WIFI_STATUS:
        online = content.get("onlinestats")
        if online is not None:
            message.online = str(online) == "1"
        return message

    status = content.get("status")
    if isinstance(status, str) and status:
        try:
            decoded = json.loads(base64.b64decode(status).decode())
            if isinstance(decoded, dict):
                message.properties.update(decoded)
        except (binascii.Error, UnicodeDecodeError, ValueError):
            _LOGGER.debug("Ignoring undecodable status in push frame")
    properties = content.get("properties")
    if isinstance(properties, dict):
        message.properties.update(properties)
    return message


class ConnectLifePush:
    """Keeps a websocket open to the ConnectLife push server."""

    def __init__(
        self,
        client: ConnectLifeClient,
        session: aiohttp.ClientSession,
        on_message: Callable[[PushMessage], None],
        task_factory: TaskFactory | None = None,
    ) -> None:
        """Create the push channel. Call async_start to connect."""
        self._client = client
        self._session = session
        self._on_message = on_message
        self._task_factory = task_factory or asyncio.create_task
        self._phone_code = str(uuid.uuid4())
        self._task: asyncio.Task | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._closing = False
        self.connected = False

    async def async_start(self) -> bool:
        """Register for push and start the connection loop."""
        try:
            await self._client.async_register_phone(self._phone_code)
            await self._client.async_get_push_channels(self._phone_code)
        except HvacError as err:
            _LOGGER.info("ConnectLife push is unavailable, polling only: %s", err)
            return False
        self._closing = False
        self._task = self._task_factory(self._run())
        return True

    async def async_stop(self) -> None:
        """Close the connection and stop reconnecting."""
        self._closing = True
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None
        self.connected = False

    async def _run(self) -> None:
        delay = BACKOFF_START
        while not self._closing:
            try:
                await self._connect_and_listen()
                delay = BACKOFF_START
            except asyncio.CancelledError:
                raise
            except (HvacError, aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("ConnectLife push disconnected: %s", err)
            self.connected = False
            if self._closing:
                break
            await asyncio.sleep(delay)
            delay = min(BACKOFF_MAX, delay * 2)

    async def _connect_and_listen(self) -> None:
        info = await self._client.async_get_push_channels(self._phone_code)
        channels = info.get("pushChannels") or []
        channel = channels[0].get("pushChannel") if channels else None
        host = info.get("pushServerIp")
        port = info.get("pushServerSslPort")
        if not channel or not host or not port:
            raise ValueError("push server details missing")
        heartbeat = float(info.get("hbInterval") or 30)
        token = await self._client.async_access_token()
        url = f"wss://{host}:{port}/ws/{channel}?phoneCode={self._phone_code}&token={token}"

        async with self._session.ws_connect(url, heartbeat=heartbeat) as ws:
            self._ws = ws
            self.connected = True
            _LOGGER.debug("ConnectLife push connected")
            async for msg in ws:
                if msg.type is aiohttp.WSMsgType.TEXT:
                    message = decode_frame(msg.data)
                    if message is not None:
                        self._on_message(message)
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
        self._ws = None
