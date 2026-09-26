"""Warnings before step 7: autotargeting, networks, mass state, geo setting, revenue."""

import httpx
import pytest

from directai_mcp.catalog.registry import Ctx
from directai_mcp.catalog.stats import CustomParams, StatsParams, _context
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import PLANS, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"


def _ctx(tmp_path, guard=True):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=guard,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _mock_campaigns(respx_mock, items, version="v5"):
    respx_mock.post(f"https://api.direct.yandex.com/json/{version}/campaigns").mock(
        return_value=_ok({"Campaigns": items})
    )


def _mock_kw_group(respx_mock):
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_ok({"AdGroups": [{"Id": 10, "CampaignId": 2}]})
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_ok({"Campaigns": [{"Id": 2}]})
    )
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": []})
    )


async def test_keywords_add_autotargeting_warning(tmp_path, respx_mock):
    _mock_kw_group(respx_mock)
    ctx = _ctx(tmp_path, guard=False)
    out = await do_plan_write(
        ctx, "keywords_add",
        {"account": "t", "adgroup_id": 10,
         "keywords": [{"text": "---autotargeting"}]},
    )
    assert "со всеми категориями" in out
    assert "acknowledge_warnings=true" in out


async def test_keywords_add_plain_no_warning(tmp_path, respx_mock):
    _mock_kw_group(respx_mock)
    ctx = _ctx(tmp_path, guard=False)
    out = await do_plan_write(
        ctx, "keywords_add",
        {"account": "t", "adgroup_id": 10, "keywords": [{"text": "грунт"}]},
    )
    assert "acknowledge_warnings" not in out


async def test_campaigns_state_mass_warning(tmp_path, respx_mock):
    _mock_campaigns(respx_mock, [
        {"Id": i, "Name": "[TEST DirectAI] x", "State": "ON"} for i in (1, 2, 3, 4)
    ])
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "campaigns_state",
        {"account": "t", "campaign_ids": [1, 2, 3, 4], "operation": "suspend"},
    )
    assert "сразу для 4 кампаний" in out
    assert "acknowledge_warnings=true" in out


async def test_campaigns_state_three_no_warning(tmp_path, respx_mock):
    _mock_campaigns(respx_mock, [
        {"Id": i, "Name": "[TEST DirectAI] x", "State": "ON"} for i in (1, 2, 3)
    ])
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "campaigns_state",
        {"account": "t", "campaign_ids": [1, 2, 3], "operation": "suspend"},
    )
    assert "acknowledge_warnings" not in out


async def test_campaigns_update_strategy_blocked_by_policy(tmp_path, respx_mock):
    # v1.1.34: смена стратегии запрещена политикой везде — до prepare/API.
    _mock_campaigns(respx_mock, [{
        "Id": 100, "Name": "[TEST DirectAI] x",
        "TextCampaign": {"BiddingStrategy": {
            "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
            "Network": {"BiddingStrategyType": "SERVING_OFF"}}},
    }])
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "campaigns_update",
        {"account": "t", "campaign_ids": [100],
         "strategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                      "Network": {"BiddingStrategyType": "NETWORK_DEFAULT"}}},
    )
    assert out.startswith(
        "Заблокировано защитой: Изменение бюджета запрещено политикой.")


async def test_campaigns_get_geo_interest(tmp_path, respx_mock):
    _mock_campaigns(respx_mock, [{
        "Id": 100, "Name": "[TEST DirectAI] x", "Type": "TEXT_CAMPAIGN",
        "State": "OFF", "Status": "DRAFT", "TimeZone": "Europe/Moscow",
        "TextCampaign": {"Settings": [
            {"Option": "ENABLE_AREA_OF_INTEREST_TARGETING", "Value": "YES"}]},
    }], version="v501")
    from directai_mcp.catalog.registry import ACTIONS
    ctx = _ctx(tmp_path)
    act = ACTIONS["campaigns_get"]
    out = await act.run(ctx, act.params(account="t", campaign_ids=[100], full=True))
    assert "GeoInterest" in out
    assert "YES" in out
    assert "ENABLE_AREA_OF_INTEREST_TARGETING" in out


def test_revenue_source_labels(tmp_path):
    ctx = _ctx(tmp_path, guard=False)
    entries = list(ctx.settings.accounts.values())
    key = _context(ctx, "stats_campaigns", entries,
                   StatsParams(period="YESTERDAY", goals=["1"]), (["1"], "explicit"))
    assert "ценность целей: условная (из настроек цели, не выручка)" in key
    agg = _context(ctx, "stats_summary", entries, StatsParams(period="YESTERDAY"))
    assert "ценность целей: условная" in agg
    assert "доход:" not in agg
    custom = _context(
        ctx, "stats_custom", entries,
        CustomParams(period="YESTERDAY", field_names=["Clicks", "Cost"]),
        ([], "none"),
    )
    assert "доход:" not in custom
