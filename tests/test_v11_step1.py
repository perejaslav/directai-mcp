"""Шаг 1.1 step 1: campaigns через v501, полные поля, рубли (SPEC-v1.1)."""

import json

import httpx

from directai_mcp.catalog import campaigns as mod  # noqa: F401 (реестр)
from directai_mcp.catalog.campaigns import (
    TEXT_FIELDS,
    UNIFIED_FIELDS,
    CampaignsGetParams,
    _block,
    _fmt_goals,
    _fmt_limit,
    _fmt_package,
    _fmt_side,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import resolve_auto_goals
from directai_mcp.config import AccountEntry, Settings

V501 = "https://api.direct.yandex.com/json/v501/campaigns"

XSD_TEXT = {
    "CounterIds",
    "RelevantKeywords",
    "Settings",
    "BiddingStrategy",
    "PriorityGoals",
    "TrackingParams",
    "AttributionModel",
    "PackageBiddingStrategy",
    "CanBeUsedAsPackageBiddingStrategySource",
    "NegativeKeywordSharedSetIds",
    "WeeklyBudgetRollover",
}
XSD_UNIFIED = XSD_TEXT - {"RelevantKeywords"}


def test_field_sets_match_xsd_enums():
    assert set(TEXT_FIELDS) == XSD_TEXT
    assert set(UNIFIED_FIELDS) == XSD_UNIFIED


def test_money_limit_to_rubles():
    assert _fmt_limit("WeeklySpendLimit", 15300000000) == "15 300.00 ₽"
    assert _fmt_limit("AverageCpa", 1100000000) == "1 100.00 ₽"
    assert _fmt_limit("BidCeiling", None) == "—"


def test_non_money_kept_as_is():
    assert _fmt_limit("Crr", 50) == "50%"
    assert _fmt_limit("GoalId", 90000005) == "90000005"
    assert _fmt_limit("RoiCoef", 300) == "300"
    assert _fmt_limit("ClicksPerWeek", 100) == "100"


def test_unknown_unit_marked():
    assert "(?)" in _fmt_limit("Profitability", 9000007)


def test_side_render():
    out = _fmt_side(
        {
            "BiddingStrategyType": "HIGHEST_POSITION",
            "HighestPosition": {"WeeklySpendLimit": 15300000000},
        }
    )
    assert "HIGHEST_POSITION" in out
    assert "15 300.00 ₽" in out
    assert _fmt_side(None) == "—"
    assert _fmt_side({"BiddingStrategyType": "SERVING_OFF"}) == "SERVING_OFF"


def test_goals_with_names():
    body = {
        "PriorityGoals": {
            "Items": [
                {"GoalId": 1, "Value": 650000000, "IsMetrikaSourceOfValue": "NO"},
                {"GoalId": 2, "Value": None, "IsMetrikaSourceOfValue": "NO"},
            ]
        }
    }
    out = _fmt_goals(body, {"1": "Заказ"})
    assert "Заказ (1): 650.00 ₽" in out
    assert "2: —" in out
    assert _fmt_goals({}, {}) == "—"


def test_package_id():
    assert _fmt_package({"PackageBiddingStrategy": {"StrategyId": 99}}) == "id 99"
    assert _fmt_package({}) == "—"


def test_block_prefers_type():
    item = {
        "Type": "TEXT_CAMPAIGN",
        "TextCampaign": {"Settings": []},
        "UnifiedCampaign": {"Settings": []},
    }
    assert _block(item) == {"Settings": []}
    item2 = {"Type": "SMART_CAMPAIGN"}
    assert _block(item2) is None


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"msk": AccountEntry(alias="msk", login="agency-login")},
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _item():
    return {
        "Id": 900000003,
        "Name": "ПОИСК - Эталон",
        "Type": "TEXT_CAMPAIGN",
        "State": "ON",
        "Status": "ACCEPTED",
        "TextCampaign": {
            "BiddingStrategy": {
                "Search": {
                    "BiddingStrategyType": "HIGHEST_POSITION",
                    "HighestPosition": {"WeeklySpendLimit": 15300000000},
                },
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            },
            "PriorityGoals": {
                "Items": [
                    {"GoalId": 90000005, "Value": 650000000},
                    {"GoalId": 900000010, "Value": 250000000},
                ]
            },
            "Settings": [{"Option": "SHARED_ACCOUNT_ENABLED", "Value": "YES"}],
            "AttributionModel": "AUTO",
            "TrackingParams": "utm_source=yandex",
            "NegativeKeywordSharedSetIds": {"Items": [90000011]},
            "CounterIds": {"Items": [90000004]},
            "PackageBiddingStrategy": {"StrategyId": 77},
        },
    }


def _resp(item):
    return httpx.Response(200, json={"result": {"Campaigns": [item]}})


async def test_get_uses_v501_and_renders(respx_mock, tmp_path):
    route = respx_mock.post(V501).mock(return_value=_resp(_item()))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaigns_get"].run(
        ctx, CampaignsGetParams(account="msk", campaign_ids=[900000003])
    )
    assert route.call_count == 1
    sent = json.loads(route.calls[0].request.content)["params"]
    assert "PriorityGoals" in sent["TextCampaignFieldNames"]
    assert "AttributionModel" in sent["UnifiedCampaignFieldNames"]
    assert "TrackingParams" in sent["UnifiedCampaignFieldNames"]
    assert "PackageBiddingStrategy" in sent["TextCampaignFieldNames"]
    assert "15 300.00 ₽" in out
    assert "AUTO" in out
    assert "90000005: 650.00 ₽" in out


async def test_get_full_shows_package_and_tracking(respx_mock, tmp_path):
    respx_mock.post(V501).mock(return_value=_resp(_item()))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaigns_get"].run(
        ctx, CampaignsGetParams(account="msk", campaign_ids=[900000003], full=True)
    )
    assert "id 77" in out
    assert "utm_source=yandex" in out
    assert "SHARED_ACCOUNT_ENABLED: YES" in out


async def test_unsupported_type_marked(respx_mock, tmp_path):
    respx_mock.post(V501).mock(
        return_value=_resp({"Id": 1, "Name": "S", "Type": "SMART_CAMPAIGN"})
    )
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaigns_get"].run(
        ctx, CampaignsGetParams(account="msk", campaign_ids=[1], full=True)
    )
    assert "тип не разбирается сервером" in out


async def test_auto_goals_use_v501(respx_mock, tmp_path):
    route = respx_mock.post(V501).mock(return_value=_resp(_item()))
    ctx = _ctx(tmp_path)
    goals = await resolve_auto_goals(
        ctx, [AccountEntry(alias="msk", login="agency-login")], [900000003]
    )
    assert route.call_count == 1
    assert goals == {"agency-login": ["90000005", "900000010"]}
