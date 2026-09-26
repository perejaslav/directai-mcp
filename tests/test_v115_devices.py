"""v1.1.5: devices без кампаний, group_by-измерения, счётчики целей."""

import json

import httpx
import pytest

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.common import goal_label
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import CustomParams, localize_goal_columns
from directai_mcp.config import AccountEntry, Settings, load_settings

V501 = "https://api.direct.yandex.com/json/v501/campaigns"
RURL = "https://api.direct.yandex.com/json/v5/reports"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _tsv(body):
    return httpx.Response(200, text=body)


async def test_devices_without_campaigns_no_crash(respx_mock, tmp_path):
    """П.1: отчёт по устройствам на весь аккаунт не падает."""
    respx_mock.post(V501).mock(return_value=_ok({"Campaigns": []}))
    respx_mock.post(RURL).mock(
        return_value=_tsv("Device\tClicks\nDESKTOP\t10\nMOBILE\t5\n")
    )
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_devices"]
    out = await act.run(ctx, act.params(account="agency-login",
                                        with_conversions=False))
    assert "DESKTOP" in out and "MOBILE" in out


async def test_custom_group_by_device(respx_mock, tmp_path):
    route = respx_mock.post(RURL).mock(
        return_value=_tsv("Device\tClicks\nDESKTOP\t10\n")
    )
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_custom"]
    out = await act.run(ctx, act.params(account="agency-login",
                                        report_type="CUSTOM_REPORT",
                                        field_names=["Clicks"],
                                        group_by=["Device"],
                                        with_conversions=False))
    sent = json.loads(route.calls[0].request.content)["params"]
    assert sent["FieldNames"] == ["Device", "Clicks"]
    assert "DESKTOP" in out


async def test_custom_group_by_time_plus_dim(respx_mock, tmp_path):
    route = respx_mock.post(RURL).mock(
        return_value=_tsv("Date\tDevice\tClicks\n2026-09-20\tDESKTOP\t3\n")
    )
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_custom"]
    await act.run(ctx, act.params(account="agency-login",
                                  report_type="CUSTOM_REPORT",
                                  field_names=["Clicks"],
                                  group_by=["day", "Device"],
                                  with_conversions=False))
    sent = json.loads(route.calls[0].request.content)["params"]
    assert sent["FieldNames"] == ["Date", "Device", "Clicks"]


def test_custom_group_by_rejects_unknown():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CustomParams(report_type="CUSTOM_REPORT", field_names=["Clicks"],
                     group_by=["AgeGroup"])


def test_goal_label_counters():
    assert goal_label("1", {"1": "N"}, None) == "N (1)"
    assert goal_label("9", {}, None) == "9"
    assert goal_label("900000007", {"900000007": "Карты"},
                       {"900000007": 90000012}) == "Карты (900000007, счётчик 90000012)"


def test_localize_with_counter():
    cols, _rows = localize_goal_columns(
        ["Conversions_900000007_AUTO"], [{"Conversions_900000007_AUTO": "3"}],
        {"900000007": "Карты"}, {"900000007": 90000012})
    assert cols == ["Conversions_Карты (900000007, счётчик 90000012)_AUTO"]


def test_goals_toml_table_counters(tmp_path):
    (tmp_path / "accounts.toml").write_text(
        '[auth]\nlogin = "x"\n[aliases.a]\nlogin = "l"\n', encoding="utf-8")
    (tmp_path / "goals.toml").write_text(
        '[goals]\n1 = "Один"\n2 = {name = "Два", counter = 90000012}\n',
        encoding="utf-8")
    settings = load_settings(tmp_path / "accounts.toml")
    assert settings.goal_names == {"1": "Один", "2": "Два"}
    assert settings.goal_counters == {"2": 90000012}
