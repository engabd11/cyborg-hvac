import base64
from datetime import UTC, datetime
import hashlib
import hmac
import json

from cyborg_hvac.backends.hisense_connectlife import client as cl
from cyborg_hvac.core.errors import (
    AccountActionRequired,
    AuthFailed,
    CannotConnect,
    CommandRejected,
)
import pytest


def test_signature_matches_reference_formula():
    # Reference: Hisense's plugin signs "<appId>\n<METHOD> <path>\ndate: <date>\n
    # hi-params-encrypt: <appId>\n" with HMAC-SHA256 and the app secret.
    date = cl.gmt_date(datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert date == "Fri, 02 Jan 2026 03:04:05 GMT"
    path = "/clife-svc/pu/get_device_status_list?timeStamp=1"
    expected_string = f"APP\nGET {path}\ndate: {date}\nhi-params-encrypt: APP\n"
    assert cl.signing_string("APP", "GET", path, date) == expected_string
    expected = base64.b64encode(
        hmac.new(b"SECRET", expected_string.encode(), hashlib.sha256).digest()
    ).decode()
    headers = cl.signed_headers("APP", "SECRET", "GET", path, None, date)
    assert f'signature="{expected}"' in headers["Authorization"]
    assert headers["hi-params-encrypt"] == "APP"
    assert headers["Digest"] == f"SHA-256={cl.EMPTY_BODY_DIGEST}"


def test_body_digest():
    body = json.dumps({"a": 1})
    expected = base64.b64encode(hashlib.sha256(body.encode()).digest()).decode()
    assert cl.body_digest(body) == expected
    assert cl.body_digest("") == cl.EMPTY_BODY_DIGEST
    # The constant is the digest of an empty body.
    assert base64.b64encode(hashlib.sha256(b"").digest()).decode() == cl.EMPTY_BODY_DIGEST


def test_source_id_shape():
    source = cl.new_source_id()
    assert source.startswith("td001002000")
    assert len(source) == len("td001002000") + 32


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._text = payload if isinstance(payload, str) else json.dumps(payload)

    async def text(self):
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, data=None, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "data": data, "headers": headers})
        return FakeResponse(*self.responses.pop(0))


def make_client(responses, on_rejected=None):
    session = FakeSession(responses)
    tokens = iter(["tok-1", "tok-2", "tok-3"])

    async def provider():
        return next(tokens)

    client = cl.ConnectLifeClient(
        session, provider, source_id="td001002000abc", on_token_rejected=on_rejected
    )
    return client, session


async def test_get_sends_token_in_header_and_params_in_query():
    client, session = make_client([(200, {"resultCode": 0, "deviceList": [{"puid": "p"}]})])
    devices = await client.async_get_devices()
    assert devices == [{"puid": "p"}]
    call = session.calls[0]
    assert call["method"] == "GET"
    assert call["headers"]["accessToken"] == "tok-1"
    assert "accessToken" not in call["url"]
    assert "appId=9793620883275788" in call["url"]
    assert call["data"] is None


async def test_post_sends_token_in_body():
    client, session = make_client([(200, {"resultCode": 0, "kvMap": {"t_power": "1"}})])
    result = await client.async_set_properties("p", {"t_power": "1"})
    assert result == {"t_power": "1"}
    body = json.loads(session.calls[0]["data"])
    assert body["puid"] == "p"
    assert body["properties"] == {"t_power": "1"}
    assert body["accessToken"] == "tok-1"
    digest = session.calls[0]["headers"]["Digest"]
    assert digest == f"SHA-256={cl.body_digest(session.calls[0]['data'])}"


async def test_wrapped_response_is_unwrapped():
    client, _ = make_client([(200, {"response": {"resultCode": 0, "deviceList": []}})])
    assert await client.async_get_devices() == []


async def test_rejected_token_refreshes_once_then_retries():
    refreshed = []

    async def on_rejected():
        refreshed.append(True)

    client, session = make_client(
        [
            (200, {"resultCode": 100026, "msg": "bad token"}),
            (200, {"resultCode": 0, "deviceList": []}),
        ],
        on_rejected,
    )
    assert await client.async_get_devices() == []
    assert refreshed == [True]
    assert session.calls[1]["headers"]["accessToken"] == "tok-2"


async def test_rejected_token_twice_is_auth_failure():
    async def on_rejected():
        return None

    client, _ = make_client([(401, ""), (401, "")], on_rejected)
    with pytest.raises(AuthFailed):
        await client.async_get_devices()


async def test_nonce_failure_retries():
    client, session = make_client(
        [(200, {"resultCode": 101005}), (200, {"resultCode": 0, "deviceList": []})]
    )
    assert await client.async_get_devices() == []
    assert len(session.calls) == 2


@pytest.mark.parametrize(
    ("payload", "error"),
    [
        ({"resultCode": 160001, "msg": "Command status mutex"}, CommandRejected),
        ({"resultCode": 206001, "msg": "Pending registration"}, AccountActionRequired),
        ({"resultCode": 999, "msg": "nope"}, CannotConnect),
    ],
)
async def test_result_codes_map_to_errors(payload, error):
    client, _ = make_client([(200, payload)])
    with pytest.raises(error):
        await client.async_set_properties("p", {"t_super": "1"})


async def test_http_403_is_cannot_connect():
    client, _ = make_client([(403, "Forbidden")])
    with pytest.raises(CannotConnect):
        await client.async_get_devices()
