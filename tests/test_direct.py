"""Step 1 acceptance tests for DirectClient (SPEC 7.3-7.6, 9)."""

import httpx
import pytest

from directai_mcp.api.direct import DirectClient
from directai_mcp.api.errors import DirectError, DirectUnverifiedError

BASE = "https://api.direct.yandex.com/json/v5"


def _ok(result=None, units="5/100/1000"):
    return httpx.Response(
        200,
        json={"result": result if result is not None else {}},
        headers={"Units": units, "RequestId": "1"},
    )


def _err(code, string="boom", status=200):
    return httpx.Response(
        status,
        json={
            "error": {
                "error_code": code,
                "error_string": string,
                "error_detail": "d",
                "request_id": "r1",
            }
        },
        headers={"Units": "5/100/1000"},
    )


async def test_units_parsed_per_login(respx_mock):
    respx_mock.post(f"{BASE}/clients").mock(
        return_value=_ok({"Clients": []}, "7/200/1000")
    )
    client = DirectClient(token="t")
    try:
        await client.call("clients", "get", {"FieldNames": ["Login"]}, "agency-login")
        assert client.last_units["agency-login"].rest == 200
        assert client.last_units["agency-login"].limit == 1000
    finally:
        await client.aclose()


async def test_error_body_at_http200_raises(respx_mock):
    respx_mock.post(f"{BASE}/campaigns").mock(return_value=_err(53, "auth"))
    client = DirectClient(token="bad")
    try:
        with pytest.raises(DirectError) as e:
            await client.call("campaigns", "get", {}, "agency-login")
        assert e.value.code == 53
        assert "set-token" in e.value.human_message()
    finally:
        await client.aclose()


async def test_retry_any_method_on_52(respx_mock):
    route = respx_mock.post(f"{BASE}/campaigns").mock(
        side_effect=[_err(52, "oauth down"), _ok({"Campaigns": []})]
    )
    client = DirectClient(token="t")
    try:
        # write method also retries on 52
        await client.call("campaigns", "suspend", {"SelectionCriteria": {}}, "a")
        assert route.call_count == 2
    finally:
        await client.aclose()


async def test_no_retry_on_53(respx_mock):
    route = respx_mock.post(f"{BASE}/campaigns").mock(return_value=_err(53, "auth"))
    client = DirectClient(token="bad")
    try:
        with pytest.raises(DirectError):
            await client.call("campaigns", "get", {}, "a")
        assert route.call_count == 1
    finally:
        await client.aclose()


async def test_no_retry_on_152_points(respx_mock):
    route = respx_mock.post(f"{BASE}/campaigns").mock(return_value=_err(152, "points"))
    client = DirectClient(token="t")
    try:
        with pytest.raises(DirectError) as e:
            await client.call("campaigns", "get", {}, "a")
        assert e.value.code == 152
        assert route.call_count == 1
    finally:
        await client.aclose()


async def test_retry_get_only_for_get(respx_mock):
    route = respx_mock.post(f"{BASE}/campaigns").mock(
        side_effect=[_err(1000, "temp"), _ok({"Campaigns": []})]
    )
    client = DirectClient(token="t")
    try:
        await client.call("campaigns", "get", {}, "a")
        assert route.call_count == 2
    finally:
        await client.aclose()


async def test_write_transient_becomes_unverified_no_retry(respx_mock):
    route = respx_mock.post(f"{BASE}/campaigns").mock(return_value=_err(1000, "temp"))
    client = DirectClient(token="t")
    try:
        with pytest.raises(DirectUnverifiedError):
            await client.call("campaigns", "update", {}, "a")
        assert route.call_count == 1
    finally:
        await client.aclose()


async def test_pagination_via_limited_by(respx_mock):
    respx_mock.post(f"{BASE}/campaigns").mock(
        side_effect=[
            _ok({"Campaigns": [{"Id": 1}], "LimitedBy": 1}),
            _ok({"Campaigns": [{"Id": 2}]}),
        ]
    )
    client = DirectClient(token="t")
    try:
        items = await client.get_all(
            "campaigns",
            {"SelectionCriteria": {}, "FieldNames": ["Id"]},
            "a",
            "Campaigns",
        )
        assert [i["Id"] for i in items] == [1, 2]
    finally:
        await client.aclose()
