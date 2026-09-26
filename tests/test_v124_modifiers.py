"""v1.1.14: SmartTv без потерь, имена ретаргетинга, строка «Не заданы»."""

import httpx

import directai_mcp.catalog.bids as _b  # noqa: F401 (реестр)
from directai_mcp.catalog.bids import (
    _ALL_MOD_TYPES,
    _DETAIL_KEYS,
    _detail,
    _mod_value,
)
from directai_mcp.catalog.common import _GEO_CACHE
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

BASE = "https://api.direct.yandex.com/json/v5"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _seed_geo():
    _GEO_CACHE["GeoRegions"] = [
        {"GeoRegionId": 213, "GeoRegionName": "Москва"},
        {"GeoRegionId": 1, "GeoRegionName": "Московская область"},
    ]


def test_all_types_have_detail_block():
    """Ни один тип из API не теряет значение: блок на каждый Type."""
    for type_value in _ALL_MOD_TYPES:
        block = "".join(
            part.title()
            for part in type_value.removesuffix("_ADJUSTMENT").split("_")
        ) + "Adjustment"
        assert block in _DETAIL_KEYS, type_value


def test_smarttv_value_kept():
    item = {"Type": "SMART_TV_ADJUSTMENT",
            "SmartTvAdjustment": {"BidModifier": 0}}
    assert _detail(item) == \
        "SmartTvAdjustment: BidModifier=-100% (показы отключены)"
    assert _mod_value(item) == 0


def test_retargeting_name_format():
    item = {"Type": "RETARGETING_ADJUSTMENT",
            "RetargetingAdjustment": {"RetargetingConditionId": 9000002,
                                      "BidModifier": 120, "Enabled": "YES"}}
    assert "Аудитория с отказами (9000002)" in _detail(item, None, {9000002: "Аудитория с отказами"})
    assert "RetargetingConditionId=9000002" in _detail(item, None, None)


async def test_modifiers_get_names_and_missing(respx_mock, tmp_path):
    import json as _json

    _seed_geo()
    route = respx_mock.post(f"{BASE}/bidmodifiers").mock(return_value=_ok({
        "BidModifiers": [
            {"Id": 1, "CampaignId": 7, "AdGroupId": None, "Level": "CAMPAIGN",
             "Type": "SMART_TV_ADJUSTMENT",
             "SmartTvAdjustment": {"BidModifier": 0}},
            {"Id": 2, "CampaignId": 7, "AdGroupId": None, "Level": "CAMPAIGN",
             "Type": "RETARGETING_ADJUSTMENT",
             "RetargetingAdjustment": {"RetargetingConditionId": 9000002,
                                       "BidModifier": 120, "Enabled": "YES"}},
            {"Id": 3, "CampaignId": 7, "AdGroupId": None, "Level": "CAMPAIGN",
             "Type": "REGIONAL_ADJUSTMENT",
             "RegionalAdjustment": {"RegionId": 213, "BidModifier": 105,
                                    "Enabled": "YES"}},
        ]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(return_value=_ok({
        "RetargetingLists": [{"Id": 9000002, "Name": "Аудитория с отказами"}]}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["bid_modifiers_get"].run(
        ctx, ACTIONS["bid_modifiers_get"].params(account="t", campaign_ids=[7]))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "SmartTvAdjustmentFieldNames" in sent
    assert "SmartTvAdjustment: BidModifier=-100% (показы отключены)" in out
    assert "(0)" not in out.split("Не заданы:")[0]
    assert "Аудитория с отказами (9000002)" in out
    assert "Москва (213)" in out
    assert "Не заданы: MOBILE_ADJUSTMENT, TABLET_ADJUSTMENT, " \
        "DESKTOP_ADJUSTMENT, DESKTOP_ONLY_ADJUSTMENT, " \
        "DEMOGRAPHICS_ADJUSTMENT, VIDEO_ADJUSTMENT, SMART_AD_ADJUSTMENT, " \
        "SERP_LAYOUT_ADJUSTMENT, INCOME_GRADE_ADJUSTMENT, " \
        "AD_GROUP_ADJUSTMENT." in out
    for present in ("SMART_TV_ADJUSTMENT", "RETARGETING_ADJUSTMENT",
                    "REGIONAL_ADJUSTMENT"):
        assert present not in out.split("Не заданы:")[1]


async def test_retargeting_fallback_on_error(respx_mock, tmp_path):
    _seed_geo()
    respx_mock.post(f"{BASE}/bidmodifiers").mock(return_value=_ok({
        "BidModifiers": [
            {"Id": 2, "CampaignId": 7, "AdGroupId": None, "Level": "CAMPAIGN",
             "Type": "RETARGETING_ADJUSTMENT",
             "RetargetingAdjustment": {"RetargetingConditionId": 9000002,
                                       "BidModifier": 120, "Enabled": "YES"}},
        ]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=httpx.Response(500, json={}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["bid_modifiers_get"].run(
        ctx, ACTIONS["bid_modifiers_get"].params(account="t", campaign_ids=[7]))
    assert "RetargetingConditionId=9000002" in out
