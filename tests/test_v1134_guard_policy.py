"""v1.1.34: политика guard — бюджеты запрещены везде, боевые/TEST матрица.

  - бюджет (daily_budget, strategy) блокируется в TEST и боевой до API;
  - разрешённая операция в боевой проходит до плана;
  - запрещённая (replace, пауза кампании) — блок.
"""

import httpx

import directai_mcp.catalog.adgroups as _ag  # noqa: F401 (реестр)
import directai_mcp.catalog.ads as _ad  # noqa: F401 (реестр)
import directai_mcp.catalog.bids as _b  # noqa: F401 (реестр)
import directai_mcp.catalog.campaigns as _c  # noqa: F401 (реестр)
import directai_mcp.catalog.keywords as _k  # noqa: F401 (реестр)
import directai_mcp.catalog.negatives as _n  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety.guard import (
    BUDGET_BLOCK,
    GUARD_NOTICE,
    combat_allowed,
)
from directai_mcp.server import do_plan_write

V5 = "https://api.direct.yandex.com/json/v5"
COMBAT_ID = 900000003
COMBAT_NAME = "ПОИСК - Эталон"
TEST_ID = 900000036
MOD_ID = 900000000001


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _campaigns(respx_mock, cid, name, **extra):
    payload = httpx.Response(
        200, json={"result": {"Campaigns": [
            {"Id": cid, "Name": name, **extra}]}})
    respx_mock.post(f"{V5}/campaigns").mock(return_value=payload)
    respx_mock.post(
        "https://api.direct.yandex.com/json/v501/campaigns").mock(
        return_value=payload)


def _modifiers(respx_mock):
    respx_mock.post(f"{V5}/bidmodifiers").mock(return_value=httpx.Response(
        200, json={"result": {"BidModifiers": [
            {"Id": MOD_ID, "Type": "MOBILE_ADJUSTMENT",
             "CampaignId": COMBAT_ID, "AdGroupId": None,
             "MobileAdjustment": {"BidModifier": 0}}]}}))


def _blocked(out: str):
    assert out.startswith(f"Заблокировано защитой: {BUDGET_BLOCK}"), out
    assert out.endswith(GUARD_NOTICE)


async def test_budget_daily_blocked_test_and_combat(tmp_path):
    for cid in (TEST_ID, COMBAT_ID):
        out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
            "account": "m", "campaign_ids": [cid], "daily_budget": 500.0})
        _blocked(out)


async def test_strategy_change_blocked(tmp_path):
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
        "account": "m", "campaign_ids": [TEST_ID],
        "strategy": {"Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}})
    _blocked(out)


async def test_combat_modifier_set_reaches_plan(respx_mock, tmp_path):
    _campaigns(respx_mock, COMBAT_ID, COMBAT_NAME)
    _modifiers(respx_mock)
    out = await do_plan_write(_ctx(tmp_path), "bid_modifiers_set", {
        "account": "m", "set_items": [{"id": MOD_ID, "bid_modifier": 80}]})
    assert out.startswith("План "), out
    assert "Заблокировано защитой" not in out


async def test_combat_negatives_add_reaches_plan(respx_mock, tmp_path):
    _campaigns(respx_mock, COMBAT_ID, COMBAT_NAME,
               NegativeKeywords={"Items": []})
    out = await do_plan_write(_ctx(tmp_path), "negatives_set", {
        "account": "m", "campaign_ids": [COMBAT_ID],
        "negatives": ["тест минус"], "mode": "add"})
    assert out.startswith("План "), out


async def test_combat_negatives_replace_blocked(respx_mock, tmp_path):
    _campaigns(respx_mock, COMBAT_ID, COMBAT_NAME,
               NegativeKeywords={"Items": []})
    out = await do_plan_write(_ctx(tmp_path), "negatives_set", {
        "account": "m", "campaign_ids": [COMBAT_ID],
        "negatives": ["тест минус"], "mode": "replace"})
    assert "Заблокировано защитой" in out
    assert "вне тестового префикса" in out
    assert out.endswith(GUARD_NOTICE)


async def test_combat_campaign_pause_blocked(respx_mock, tmp_path):
    _campaigns(respx_mock, COMBAT_ID, COMBAT_NAME)
    out = await do_plan_write(_ctx(tmp_path), "campaigns_state", {
        "account": "m", "campaign_ids": [COMBAT_ID], "operation": "suspend"})
    assert "Заблокировано защитой" in out
    assert "вне тестового префикса" in out


async def test_combat_excluded_add_reaches_plan(respx_mock, tmp_path):
    _campaigns(respx_mock, COMBAT_ID, COMBAT_NAME,
               ExcludedSites={"Items": []})
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID],
        "excluded_sites": ["example.com"]})
    assert out.startswith("План "), out


def test_combat_allowed_matrix():
    assert combat_allowed("keywords_add", {}) is True
    assert combat_allowed("keywords_state", {"keyword_ids": [1]}) is True
    assert combat_allowed("ads_create", {}) is True
    assert combat_allowed("ads_update", {"ad_ids": [1]}) is True
    assert combat_allowed("bids_set", {"keyword_ids": [1]}) is True
    assert combat_allowed("bid_modifiers_set", {"set_items": [{"id": 1}]}) is True
    assert combat_allowed("bid_modifiers_set", {"delete_ids": [1]}) is False
    assert combat_allowed("negatives_set", {"mode": "add"}) is True
    assert combat_allowed("negatives_set", {"mode": "replace"}) is False
    assert combat_allowed(
        "negatives_set",
        {"mode": "add", "update_shared_set": {"id": 5}}) is False
    assert combat_allowed(
        "campaigns_update", {"excluded_sites": ["a.ru"]}) is True
    assert combat_allowed(
        "campaigns_update",
        {"excluded_sites": ["a.ru"], "end_date": "2026-12-31"}) is False
    assert combat_allowed(
        "adgroups_update",
        {"groups": [{"id": 1, "regions": ["Москва"], "regions_mode": "add",
                     "region_ids": None, "name": None, "negatives": None,
                     "tracking_params": None}]}) is True
    assert combat_allowed(
        "adgroups_update",
        {"groups": [{"id": 1, "regions": ["Москва"],
                     "regions_mode": "replace"}]}) is False
    assert combat_allowed(
        "adgroups_update",
        {"groups": [{"id": 1, "region_ids": [213]}]}) is False
    assert combat_allowed("campaigns_state", {"campaign_ids": [1]}) is False
    assert combat_allowed("ads_state", {"ad_ids": [1]}) is False
