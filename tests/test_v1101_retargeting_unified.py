"""v1.10.1: UNIFIED_AD_GROUP совместим с TEXT_AD_GROUP; цели 12/13 — отказ до API."""

import httpx

from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"
BASE_V501 = "https://api.direct.yandex.com/json/v501"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=True,
        retargeting_write_enabled=True,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _mock_target_add(respx_mock, group_type, items):
    respx_mock.post(f"{BASE_V501}/adgroups").mock(
        return_value=_ok({"AdGroups": [
            {"Id": 900000501, "CampaignId": 900000301, "Type": group_type}]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Type": "RETARGETING", "Name": "buyers",
             "IsAvailable": "YES", "Scope": "FOR_TARGETS_AND_ADJUSTMENTS",
             "AvailableForTargetsInAdGroupTypes": {"Items": items},
             "Rules": [{"Operator": "ALL",
                        "Arguments": [{"ExternalId": 1, "MembershipLifeSpan": 30}]}]},
        ]}))
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": []}))
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok({"Campaigns": [
            {"Id": 900000301, "Name": "c", "Type": "UNIFIED_CAMPAIGN",
             "UnifiedCampaign": {"BiddingStrategy": {
                 "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                 "Network": {"BiddingStrategyType": "SERVING_OFF"}}}},
        ]}))


async def test_unified_group_with_text_item_allowed(tmp_path, respx_mock):
    _mock_target_add(respx_mock, "UNIFIED_AD_GROUP",
                     ["TEXT_AD_GROUP", "MAX_ADS_AD_GROUP", "MOBILE_APP_AD_GROUP"])
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501, "retargeting_list_id": 900000701,
    })
    assert "Будет привязано" in out


async def test_unified_group_without_text_item_refused(tmp_path, respx_mock):
    _mock_target_add(respx_mock, "UNIFIED_AD_GROUP",
                     ["CPM_BANNER_AD_GROUP", "CPM_VIDEO_AD_GROUP"])
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501, "retargeting_list_id": 900000701,
    })
    assert "нельзя привязать" in out


async def test_goal_12_refused_before_api(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_create", {
        "account": "t", "name": "[TEST DirectAI] x",
        "rules": [{"operator": "ALL",
                   "arguments": [{"external_id": 12, "membership_life_span": 30}]}],
    })
    assert "служебная цель" in out
    assert "8800" in out
    assert respx_mock.calls == []


async def test_goal_13_update_refused_before_api(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Type": "RETARGETING", "Name": "n",
             "Rules": [{"Operator": "ALL",
                        "Arguments": [{"ExternalId": 1, "MembershipLifeSpan": 30}]}]},
        ]}))
    ctx = _ctx(tmp_path)
    calls_before = len(respx_mock.calls)
    out = await do_plan_write(ctx, "retargeting_list_update", {
        "account": "t", "list_id": 900000701,
        "rules": [{"operator": "ALL",
                   "arguments": [{"external_id": 13, "membership_life_span": 30}]}],
    })
    assert "служебная цель" in out
    assert "8800" in out
    # update-префлайт читает текущее условие (1 вызов), записи add/update быть не должно
    assert len(respx_mock.calls) == calls_before + 1
