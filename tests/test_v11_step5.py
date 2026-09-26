"""Шаг 1.1-5 step 5: учёт запросов/Units, мета-строка, порог (SPEC-v1.1)."""

import httpx

import directai_mcp.catalog.accounts as _acc  # noqa: F401 (реестр)
import directai_mcp.catalog.bids as _bids  # noqa: F401 (реестр)
from directai_mcp.api.direct import DirectClient, NetStats
from directai_mcp.api.reports import ReportsClient
from directai_mcp.catalog.common import finalize, net_summary
from directai_mcp.catalog.registry import Ctx, categories
from directai_mcp.config import AccountEntry, Settings

V5G = "https://api.direct.yandex.com/json/v5/campaigns"
RURL = "https://api.direct.yandex.com/json/v5/reports"


def _ok(result=None, units="5/100/1000"):
    headers = {"RequestId": "1"}
    if units is not None:
        headers["Units"] = units
    return httpx.Response(
        200, json={"result": result if result is not None else {}}, headers=headers
    )


def _err(code, units="5/100/1000"):
    return httpx.Response(
        200,
        json={"error": {"error_code": code, "error_string": "x",
                        "error_detail": "d", "request_id": "r"}},
        headers={"Units": units},
    )


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"a": AccountEntry(alias="a", login="l")},
        reports_dir=tmp_path / "reports",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


async def test_missing_units_header(respx_mock):
    respx_mock.post(V5G).mock(return_value=_ok({"Campaigns": []}, units=None))
    client = DirectClient(token="t")
    try:
        await client.call("campaigns", "get", {}, "l")
        assert client.stats.requests == 1
        assert client.stats.units_used == 0
        assert client.stats.rests == {}
    finally:
        await client.aclose()


async def test_broken_units_header(respx_mock):
    respx_mock.post(V5G).mock(return_value=_ok({"Campaigns": []}, units="abc"))
    client = DirectClient(token="t")
    try:
        await client.call("campaigns", "get", {}, "l")
        assert client.stats.units_used == 0
        assert client.stats.rests == {}
    finally:
        await client.aclose()


async def test_retry_counted_by_code(respx_mock):
    route = respx_mock.post(V5G).mock(
        side_effect=[_err(52), _ok({"Campaigns": []})]
    )
    client = DirectClient(token="t")
    try:
        await client.call("campaigns", "get", {}, "l")
        assert route.call_count == 2
        assert client.stats.requests == 2
        assert client.stats.retries_code == 1
        assert client.stats.retries_net == 0
        assert client.stats.units_used == 10
    finally:
        await client.aclose()


async def test_write_no_retry_on_1000(respx_mock):
    from directai_mcp.api.errors import DirectUnverifiedError

    respx_mock.post(V5G).mock(return_value=_err(1000))
    client = DirectClient(token="t")
    try:
        try:
            await client.call("campaigns", "suspend", {}, "l")
        except DirectUnverifiedError:
            pass
        else:
            raise AssertionError("expected unverified")
        assert client.stats.requests == 1
        assert client.stats.retries_code == 0
    finally:
        await client.aclose()


async def test_reports_polls_and_wait(respx_mock):
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    respx_mock.post(RURL).mock(
        side_effect=[
            httpx.Response(201, text="", headers={"retryIn": "3"}),
            httpx.Response(202, text="", headers={"retryIn": "4"}),
            httpx.Response(200, text="Clicks\n10\n"),
        ]
    )
    client = ReportsClient(token="t", sleep=fake_sleep)
    try:
        await client.fetch("l", {"FieldNames": ["Clicks"]})
        assert client.stats.requests == 3
        assert client.stats.polls == 2
        assert client.stats.wait_sec == 7.0
        assert waits == [3.0, 4.0]
    finally:
        await client.aclose()


def test_net_summary_single(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.net = NetStats(requests=3, retries_code=1, units_used=40,
                       rests={"l": (960, 1000)})
    line = net_summary(ctx)
    assert line.startswith("API: 3 запроса (1 повтор), Units израсходовано 40")
    assert "остаток: 960/1000 (l)" in line


def test_net_summary_multi_min_and_warnings(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.net = NetStats(requests=5, units_used=50,
                       rests={"a": (5, 1000), "b": (900, 1000)})
    line = net_summary(ctx)
    assert "мин. 5/1000 (a), кабинетов 2" in line
    assert "ниже 10%: a" in line


def test_net_summary_no_header(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.net = NetStats(requests=2, direct_requests=2)
    assert "н/д (заголовок отсутствует)" in net_summary(ctx)
    ctx2 = _ctx(tmp_path)
    assert net_summary(ctx2) == ""


def test_net_summary_reports_free(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.net = NetStats(requests=2)
    assert "Reports: баллы не расходуются" in net_summary(ctx)


def test_plural_forms(tmp_path):
    from directai_mcp.catalog.common import _plural

    assert _plural(1, "запрос", "запроса", "запросов") == "запрос"
    assert _plural(2, "запрос", "запроса", "запросов") == "запроса"
    assert _plural(5, "запрос", "запроса", "запросов") == "запросов"
    assert _plural(11, "повтор", "повтора", "повторов") == "повторов"
    assert _plural(21, "повтор", "повтора", "повторов") == "повтор"
    ctx = _ctx(tmp_path)
    ctx.net = NetStats(requests=1, direct_requests=1, units_used=11,
                       rests={"l": (989, 1000)})
    assert net_summary(ctx).startswith("API: 1 запрос (0 повторов)")


def test_net_line_in_finalize(tmp_path):
    ctx = _ctx(tmp_path)
    client = DirectClient(token="t")
    client.stats.requests = 1
    client.stats.units_used = 7
    client.stats.rests = {"l": (993, 1000)}
    ctx._clients.append(client)
    out = finalize(ctx, "c", "act", ["Id"], [{"Id": 1}], None, None, [])
    assert "API: 1 запрос (0 повторов), Units израсходовано 7" in out
    assert "остаток: 993/1000 (l)" in out


def test_categories_bid_modifiers():
    cats = categories()
    assert "- bid_modifiers:" in cats
    assert "\n- bid:" not in cats


def test_warn_pct_default():
    assert Settings(auth_login="x").units_warn_pct == 10
