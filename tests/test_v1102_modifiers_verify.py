"""v1.10.2: verify подтверждает корректировки с BidModifier=0 (живой журнал #73)."""

import json

import httpx

from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.server import do_apply_write, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=True,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _mock_scope(respx_mock):
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_ok({"AdGroups": [{"Id": 900000501, "CampaignId": 900000301}]})
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_ok({"Campaigns": [{"Id": 900000301, "Name": "[TEST DirectAI] c"}]})
    )


def _plan_id(text):
    return text.split()[1].rstrip(":")


async def test_retargeting_zero_verified(tmp_path, respx_mock):
    """RETARGETING BidModifier=0: apply OK + read-back подтверждён (кейс #73)."""
    _mock_scope(respx_mock)
    respx_mock.post(f"{BASE}/bidmodifiers").mock(side_effect=[
        _ok({"AddResults": [{}]}),
        _ok({"BidModifiers": [{
            "Id": 900000701, "CampaignId": 900000301, "AdGroupId": 900000501,
            "Level": "AD_GROUP", "Type": "RETARGETING_ADJUSTMENT",
            "RetargetingAdjustment": {"RetargetingConditionId": 900000801,
                                      "BidModifier": 0},
        }]}),
    ])
    ctx = _ctx(tmp_path)
    plan_text = await do_plan_write(ctx, "bid_modifiers_set", {
        "account": "t",
        "add_items": [{"adgroup_id": 900000501, "kind": "RETARGETING",
                       "retargeting_condition_id": 900000801, "bid_modifier": 0}],
    })
    assert "полностью отключены" in plan_text
    out = await do_apply_write(ctx, _plan_id(plan_text), acknowledge_warnings=True)
    assert "подтверждено read-back" in out
    # Моки отдают блок вне зависимости от FieldNames, поэтому дополнительно
    # проверяем, что verify реально запросил RetargetingAdjustment у API:
    # на старом коде его не было в _MOD_SUBFIELDS (причина журнала #73).
    bodies = [json.loads(c.request.content)["params"]
              for c in respx_mock.calls
              if c.request.url.path.endswith("/bidmodifiers")]
    assert any("RetargetingAdjustmentFieldNames" in b for b in bodies)


async def test_desktop_only_zero_verified(tmp_path, respx_mock):
    """DESKTOP_ONLY BidModifier=0: тот же класс ошибки (невидимый блок в get)."""
    _mock_scope(respx_mock)
    respx_mock.post(f"{BASE}/bidmodifiers").mock(side_effect=[
        _ok({"AddResults": [{}]}),
        _ok({"BidModifiers": [{
            "Id": 900000702, "CampaignId": 900000301, "AdGroupId": None,
            "Level": "CAMPAIGN", "Type": "DESKTOP_ONLY_ADJUSTMENT",
            "DesktopOnlyAdjustment": {"BidModifier": 0},
        }]}),
    ])
    ctx = _ctx(tmp_path)
    plan_text = await do_plan_write(ctx, "bid_modifiers_set", {
        "account": "t",
        "add_items": [{"campaign_id": 900000301, "kind": "DESKTOP_ONLY",
                       "bid_modifier": 0}],
    })
    out = await do_apply_write(ctx, _plan_id(plan_text), acknowledge_warnings=True)
    assert "подтверждено read-back" in out
