"""Step 6 tests: bids_set, bid_modifiers_set (SPEC 9)."""

import httpx
import pytest

from directai_mcp.catalog import bids as bids_mod
from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

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


def _mock_scope(respx_mock, campaign_name="[TEST DirectAI] x"):
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": [{"Id": 1, "AdGroupId": 10}]})
    )
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_ok({"AdGroups": [{"Id": 10, "CampaignId": 100}]})
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_ok({"Campaigns": [{"Id": 100, "Name": campaign_name}]})
    )


def test_rubles_to_micros():
    assert bids_mod.rubles_to_micros(0.3) == 300000
    assert bids_mod.rubles_to_micros(0.35) == 350000


async def test_bids_set_validation(tmp_path):
    ctx = _ctx(tmp_path, guard=False)
    out = await do_plan_write(ctx, "bids_set", {"account": "t"})
    assert "keyword_ids, adgroup_ids или campaign_ids" in out
    out = await do_plan_write(
        ctx, "bids_set", {"account": "t", "keyword_ids": [1]}
    )
    assert "search_bid или network_bid" in out


async def test_bids_set_preview_and_ratio_warning(tmp_path, respx_mock):
    _mock_scope(respx_mock)
    respx_mock.post(f"{BASE}/keywordbids").mock(
        return_value=_ok({
            "KeywordBids": [
                {"KeywordId": 1, "AdGroupId": 10, "CampaignId": 100,
                 "Search": {"Bid": 300000}, "Network": {"Bid": 300000}}
            ]
        })
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "bids_set",
        {"account": "t", "keyword_ids": [1], "search_bid": 0.80},
    )
    assert "0.30 → 0.80" in out
    assert "изменение в 2.7 раза (порог 2.0)" in out
    assert "acknowledge_warnings=true" in out


async def test_bids_set_guard_blocks_foreign(tmp_path, respx_mock):
    _mock_scope(respx_mock, campaign_name="PROD campaign")
    respx_mock.post(f"{BASE}/keywordbids").mock(
        return_value=_ok({"KeywordBids": []})
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "bids_set",
        {"account": "t", "keyword_ids": [1], "search_bid": 0.50},
    )
    assert "Заблокировано защитой" in out


async def test_modifiers_set_unknown_region(tmp_path, respx_mock):
    _mock_scope(respx_mock)
    respx_mock.post(f"{BASE}/bidmodifiers").mock(
        return_value=_ok({"BidModifiers": []})
    )
    respx_mock.post(f"{BASE}/dictionaries").mock(
        return_value=_ok({"GeoRegions": [{"GeoRegionId": 213, "GeoRegionName": "Москва"}]})
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "bid_modifiers_set",
        {"account": "t",
         "add_items": [{"campaign_id": 100, "kind": "REGIONAL",
                        "region": "Атлантида", "bid_modifier": 130}]},
    )
    assert "не найден" in out


async def test_modifiers_add_preview_region_name(tmp_path, respx_mock):
    _mock_scope(respx_mock)
    respx_mock.post(f"{BASE}/bidmodifiers").mock(
        return_value=_ok({"BidModifiers": []})
    )
    respx_mock.post(f"{BASE}/dictionaries").mock(
        return_value=_ok({"GeoRegions": [{"GeoRegionId": 213, "GeoRegionName": "Москва"}]})
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "bid_modifiers_set",
        {"account": "t",
         "add_items": [{"campaign_id": 100, "kind": "REGIONAL",
                        "region": "Москва", "bid_modifier": 130}]},
    )
    assert "Москва (213)" in out
    assert "+30%" in out


async def test_modifiers_delete_missing_blocked(tmp_path, respx_mock):
    _mock_scope(respx_mock)
    respx_mock.post(f"{BASE}/bidmodifiers").mock(
        return_value=_ok({"BidModifiers": []})
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "bid_modifiers_set",
        {"account": "t", "delete_ids": [999]},
    )
    assert "не найдена" in out


async def test_apply_unknown_plan_id(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_apply_write(ctx, "deadbeef1234")
    assert "неизвестен, просрочен или уже применён" in out
