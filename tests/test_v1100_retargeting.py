"""B4 v1.10.0: retargeting reads + writes (mocks, no live calls)."""

import httpx

from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import do_apply_write, do_plan_write, plans_for

BASE = "https://api.direct.yandex.com/json/v5"
BASE_V501 = "https://api.direct.yandex.com/json/v501"


def _ctx(tmp_path, guard=True, retargeting=True):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=guard,
        retargeting_write_enabled=retargeting,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _run(name, ctx, params):
    act = ACTIONS[name]
    validated = act.params.model_validate(params)
    assert act.run is not None
    return act.run(ctx, validated)


def _lists_payload():
    return {"RetargetingLists": [
        {"Id": 900000701, "Type": "RETARGETING", "Name": "[TEST DirectAI] buyers",
         "Description": "d", "IsAvailable": "YES",
         "Scope": "FOR_TARGETS_AND_ADJUSTMENTS",
         "AvailableForTargetsInAdGroupTypes": {"Items": ["TEXT_AD_GROUP"]},
         "Rules": [
             {"Operator": "ALL",
              "Arguments": [{"ExternalId": 900000801, "MembershipLifeSpan": 30}]},
             {"Operator": "NONE",
              "Arguments": [{"ExternalId": 900000802, "MembershipLifeSpan": 30}]},
         ]},
        {"Id": 900000702, "Type": "AUDIENCE", "Name": "short interests",
         "IsAvailable": "YES", "Scope": "FOR_TARGETS_ONLY",
         "AvailableForTargetsInAdGroupTypes": {"Items": ["TEXT_AD_GROUP"]},
         "Rules": [{"Operator": "ANY",
                     "Arguments": [{"ExternalId": 102499680007}]}]},
    ]}


async def test_lists_human_rules_and_usage(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok(_lists_payload()))
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": [
            {"Id": 900000901, "AdGroupId": 900000501, "CampaignId": 900000301,
             "RetargetingListId": 900000701},
        ]}))
    ctx = _ctx(tmp_path)
    out = await _run("retargeting_lists_list", ctx, {"account": "t"})
    assert "выполнили всё" in out
    assert "НЕ выполнили ничего" in out
    assert "краткосрочный интерес 2499680007" in out
    assert "таргетинг 900000901" in out
    assert "не используется" in out


async def test_targets_list_names(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": [
            {"Id": 900000901, "AdGroupId": 900000501, "CampaignId": 900000301,
             "RetargetingListId": 900000701, "InterestId": None,
             "State": "ON", "ContextBid": 7000000, "StrategyPriority": "NORMAL"},
        ]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Name": "[TEST DirectAI] buyers"}]}))
    ctx = _ctx(tmp_path)
    out = await _run("audience_targets_list", ctx,
                     {"account": "t", "campaign_id": 900000301})
    assert "[TEST DirectAI] buyers (900000701)" in out
    assert "AND с фразами" in out


async def test_create_valid_preview(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_create", {
        "account": "t", "name": "[TEST DirectAI] site visitors",
        "rules": [{"operator": "ALL",
                   "arguments": [{"external_id": 900000801,
                                  "membership_life_span": 30}]}],
    })
    assert "План" in out
    assert "выполнили всё" in out
    assert "AND с фразами" in out
    assert "исключить" in out.lower()


async def test_create_only_none_warns(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_create", {
        "account": "t", "name": "[TEST DirectAI] non buyers",
        "rules": [{"operator": "NONE",
                   "arguments": [{"external_id": 900000801,
                                  "membership_life_span": 30}]}],
    })
    assert "только для корректировок" in out


async def test_create_audience_without_pos_refused(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_create", {
        "account": "t", "name": "x", "list_type": "AUDIENCE",
        "rules": [{"operator": "NONE",
                   "arguments": [{"external_id": 900000801}]}],
    })
    assert "хотя бы одно правило ALL/ANY" in out


async def test_create_bad_lifespan_refused(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_create", {
        "account": "t", "name": "x",
        "rules": [{"operator": "ALL",
                   "arguments": [{"external_id": 900000801,
                                  "membership_life_span": 600}]}],
    })
    assert "540" in out


async def test_update_class_change_refused(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Type": "RETARGETING", "Name": "n",
             "Rules": [{"Operator": "ALL",
                        "Arguments": [{"ExternalId": 1, "MembershipLifeSpan": 30}]}]},
        ]}))
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_update", {
        "account": "t", "list_id": 900000701,
        "rules": [{"operator": "NONE", "arguments": [{"external_id": 1}]}],
    })
    assert "класс условия" in out


async def test_delete_used_refused_with_groups(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Name": "buyers", "Type": "RETARGETING"}]}))
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": [
            {"Id": 900000901, "AdGroupId": 900000501, "CampaignId": 900000301,
             "RetargetingListId": 900000701},
        ]}))
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_delete",
                              {"account": "t", "list_id": 900000701})
    assert "используется" in out
    assert "900000501" in out


async def test_delete_non_test_warns(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Name": "buyers", "Type": "RETARGETING"}]}))
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": []}))
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "retargeting_list_delete",
                              {"account": "t", "list_id": 900000701})
    assert "acknowledge_warnings=true" in out


def _mock_target_add(respx_mock, group_type="TEXT_AD_GROUP", scope="FOR_TARGETS_AND_ADJUSTMENTS",
                     strategy=("HIGHEST_POSITION", None), available="YES",
                     existing=()):
    respx_mock.post(f"{BASE_V501}/adgroups").mock(
        return_value=_ok({"AdGroups": [
            {"Id": 900000501, "CampaignId": 900000301, "Type": group_type}]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok({"RetargetingLists": [
            {"Id": 900000701, "Type": "RETARGETING", "Name": "buyers",
             "IsAvailable": available, "Scope": scope,
             "AvailableForTargetsInAdGroupTypes": {"Items": ["TEXT_AD_GROUP"]},
             "Rules": [{"Operator": "ALL",
                        "Arguments": [{"ExternalId": 1, "MembershipLifeSpan": 30}]}]},
        ]}))
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": list(existing)}))
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok({"Campaigns": [
            {"Id": 900000301, "Name": "c", "Type": "TEXT_CAMPAIGN",
             "TextCampaign": {"BiddingStrategy": {
                 "Search": {"BiddingStrategyType": strategy[0]},
                 "Network": {"BiddingStrategyType": strategy[1]}}}},
        ]}))


async def test_target_add_auto_ignores_bid(tmp_path, respx_mock):
    _mock_target_add(respx_mock, strategy=("OPTIMIZATION_CONVERSIONS", None))
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501,
        "retargeting_list_id": 900000701, "context_bid": 5.0,
    })
    assert "проигнорирована" in out
    assert "приоритет NORMAL" in out


async def test_target_add_manual_bid_cap(tmp_path, respx_mock):
    _mock_target_add(respx_mock)
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501,
        "retargeting_list_id": 900000701, "context_bid": 99999.0,
    })
    assert "санитарного лимита" in out


async def test_target_add_incompatible_group(tmp_path, respx_mock):
    _mock_target_add(respx_mock, group_type="CPM_BANNER_AD_GROUP")
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501,
        "retargeting_list_id": 900000701,
    })
    assert "нельзя привязать" in out


async def test_target_add_adjustments_only_refused(tmp_path, respx_mock):
    _mock_target_add(respx_mock, scope="FOR_ADJUSTMENTS_ONLY")
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501,
        "retargeting_list_id": 900000701,
    })
    assert "только для корректировок" in out


async def test_target_add_duplicate_refused(tmp_path, respx_mock):
    _mock_target_add(respx_mock, existing=[
        {"Id": 900000901, "RetargetingListId": 900000701}])
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_add", {
        "account": "t", "adgroup_id": 900000501,
        "retargeting_list_id": 900000701,
    })
    assert "уже привязано" in out


async def test_target_state_suspend(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": [
            {"Id": 900000901, "AdGroupId": 900000501,
             "RetargetingListId": 900000701, "State": "ON"},
        ]}))
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "audience_target_state", {
        "account": "t", "target_ids": [900000901], "operation": "suspend",
    })
    assert "SUSPENDED" in out


async def test_modifiers_retargeting_minus100_warns(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_ok({"AdGroups": [{"Id": 900000501, "CampaignId": 900000301}]})
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_ok({"Campaigns": [{"Id": 900000301, "Name": "[TEST DirectAI] c"}]})
    )
    ctx = _ctx(tmp_path)
    out = await do_plan_write(ctx, "bid_modifiers_set", {
        "account": "t",
        "add_items": [{"campaign_id": 900000301, "kind": "RETARGETING",
                       "retargeting_condition_id": 900000701, "bid_modifier": 0}],
    })
    assert "полностью отключены" in out


async def test_effective_multiplier_a4():
    from directai_mcp.catalog.tracking import calc_effective_multiplier

    mult, _note = calc_effective_multiplier([
        {"type": "RETARGETING_ADJUSTMENT", "raw": 0, "level": "campaign"},
        {"type": "MOBILE_ADJUSTMENT", "raw": 120, "level": "campaign"},
    ])
    assert mult is not None and abs(mult - 1.2) < 1e-9


async def test_flag_off_blocks_writes_but_not_reads(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(
        return_value=_ok(_lists_payload()))
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": []}))
    ctx = _ctx(tmp_path, retargeting=False)
    for action_name, params in [
        ("retargeting_list_create", {"account": "t", "name": "n",
                                    "rules": [{"operator": "ALL",
                                               "arguments": [{"external_id": 1}]}]}),
        ("retargeting_list_update", {"account": "t", "list_id": 1, "name": "n"}),
        ("retargeting_list_delete", {"account": "t", "list_id": 1}),
        ("audience_target_add", {"account": "t", "adgroup_id": 1,
                                 "retargeting_list_id": 1}),
        ("audience_target_state", {"account": "t", "target_ids": [1],
                                   "operation": "suspend"}),
    ]:
        out = await do_plan_write(ctx, action_name, params)
        assert "write_enabled=false" in out, action_name
    out = await _run("retargeting_lists_list", ctx, {"account": "t"})
    assert "условий 2" in out


async def test_create_end_to_end_readback(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/retargetinglists").mock(side_effect=[
        _ok({"AddResults": [{"Id": 900000703}]}),
        _ok({"RetargetingLists": [{"Id": 900000703, "Name": "[TEST DirectAI] n"}]}),
    ])
    ctx = _ctx(tmp_path)
    store = plans_for(ctx)
    plan_text = await do_plan_write(ctx, "retargeting_list_create", {
        "account": "t", "name": "[TEST DirectAI] n",
        "rules": [{"operator": "ALL", "arguments": [{"external_id": 1}]}],
    })
    plan_id = plan_text.split()[1].rstrip(":")
    assert store.peek(plan_id) is not None
    out = await do_apply_write(ctx, plan_id)
    assert "подтверждено read-back" in out


async def test_actions_registered():
    for name in ("retargeting_lists_list", "audience_targets_list",
                 "retargeting_list_create", "retargeting_list_update",
                 "retargeting_list_delete", "audience_target_add",
                 "audience_target_state"):
        assert name in ACTIONS, name
        assert ACTIONS[name].mode in ("read", "write")
