"""v1.8.0: запись создаёт ЕПК (UNIFIED), а не legacy TEXT.

- campaigns_create по умолчанию → UnifiedCampaign + json/v501;
  явный TEXT_CAMPAIGN → v5 + предупреждение «устаревший тип».
- campaigns_update: блок стратегии по типу кампании, v501 при ЕПК.
- adgroups_create: резолв типа кампании (v501), отказ при неизвестной.
- ads_create: дефолт ad_type по контенту, совместимость группы/кампании
  до API, DisplayUrlPath обязателен и сверяется в read-back.
- moderate: в цепочке создания отсутствует, guard блокирует по умолчанию.
"""

import httpx
import pytest

import directai_mcp.catalog.adgroups as _g  # noqa: F401 (реестр)
import directai_mcp.catalog.ads as _a  # noqa: F401 (реестр)
import directai_mcp.catalog.campaigns as _c  # noqa: F401 (реестр)
import directai_mcp.catalog.keywords as _k  # noqa: F401 (реестр)
from directai_mcp.catalog.common import split_request
from directai_mcp.server import PLANS, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"
BASE_V501 = "https://api.direct.yandex.com/json/v501"


def _ctx(tmp_path, guard=True):
    from directai_mcp.catalog.registry import Ctx
    from directai_mcp.config import AccountEntry, Settings

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


def _types(
    respx_mock,
    group_type="TEXT_AD_GROUP",
    campaign_type="TEXT_CAMPAIGN",
    group_id=5,
    campaign_id=2,
):
    """Типы группы/кампании для резолва в prepare (v501)."""
    respx_mock.post(f"{BASE_V501}/adgroups").mock(
        return_value=_ok(
            {
                "AdGroups": [
                    {"Id": group_id, "CampaignId": campaign_id, "Type": group_type}
                ]
            }
        )
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok(
            {"Campaigns": [{"Id": campaign_id, "Name": "K", "Type": campaign_type}]}
        )
    )


def _pid(out):
    assert out.startswith("План "), out
    return out.split()[1].rstrip(":")


# --- campaigns_create: ЕПК по умолчанию ---


async def test_create_default_is_unified_v501(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path),
        "campaigns_create",
        {"account": "t", "name": "[TEST DirectAI] ЕПК"},
    )
    pid = _pid(out)
    _service, _method, body, version = split_request(PLANS.peek(pid).requests[0])
    assert (_service, _method, version) == ("campaigns", "add", "v501")
    assert "UnifiedCampaign" in body["Campaigns"][0]
    assert "TextCampaign" not in body["Campaigns"][0]
    assert "UNIFIED_CAMPAIGN" in out


async def test_create_explicit_text_warns_legacy_v5(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path),
        "campaigns_create",
        {
            "account": "t",
            "name": "[TEST DirectAI] Легаси",
            "campaign_type": "TEXT_CAMPAIGN",
        },
    )
    pid = _pid(out)
    _service, _method, body, version = split_request(PLANS.peek(pid).requests[0])
    assert version == "v5"
    assert "TextCampaign" in body["Campaigns"][0]
    assert "устаревший тип" in out


# --- campaigns_update: блок стратегии и версия по типу ---


async def test_update_strategy_routed_by_type(respx_mock, tmp_path):
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok(
            {
                "Campaigns": [
                    {"Id": 7, "Name": "ЕПК", "Type": "UNIFIED_CAMPAIGN"},
                    {"Id": 8, "Name": "Текст", "Type": "TEXT_CAMPAIGN"},
                ]
            }
        )
    )
    out = await do_plan_write(
        _ctx(tmp_path, guard=False),
        "campaigns_update",
        {
            "account": "t",
            "campaign_ids": [7, 8],
            "strategy": {
                "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            },
        },
    )
    pid = _pid(out)
    _service, _method, body, version = split_request(PLANS.peek(pid).requests[0])
    assert version == "v501"
    by_id = {c["Id"]: c for c in body["Campaigns"]}
    assert "UnifiedCampaign" in by_id[7] and "TextCampaign" not in by_id[7]
    assert "TextCampaign" in by_id[8] and "UnifiedCampaign" not in by_id[8]


async def test_update_text_only_stays_v5(respx_mock, tmp_path):
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok(
            {"Campaigns": [{"Id": 8, "Name": "Текст", "Type": "TEXT_CAMPAIGN"}]}
        )
    )
    out = await do_plan_write(
        _ctx(tmp_path, guard=False),
        "campaigns_update",
        {"account": "t", "campaign_ids": [8], "end_date": "2026-12-31"},
    )
    pid = _pid(out)
    assert PLANS.peek(pid).requests[0][3] == "v5"


async def test_update_unknown_campaign_rejected(respx_mock, tmp_path):
    respx_mock.post(f"{BASE_V501}/campaigns").mock(return_value=_ok({"Campaigns": []}))
    out = await do_plan_write(
        _ctx(tmp_path, guard=False),
        "campaigns_update",
        {"account": "t", "campaign_ids": [7], "end_date": "2026-12-31"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "не найдены" in out


# --- adgroups_create: резолв типа, отказ при неизвестной ---


async def test_adgroups_create_marks_unified(respx_mock, tmp_path):
    _types(respx_mock, campaign_type="UNIFIED_CAMPAIGN")
    out = await do_plan_write(
        _ctx(tmp_path),
        "adgroups_create",
        {
            "account": "t",
            "groups": [{"campaign_id": 2, "name": "Группа", "region_ids": [213]}],
        },
    )
    pid = _pid(out)
    assert "[ЕПК]" in out
    req = PLANS.peek(pid).requests[0]
    assert (req[0], req[1]) == ("adgroups", "add")


async def test_adgroups_create_unknown_campaign_rejected(respx_mock, tmp_path):
    respx_mock.post(f"{BASE_V501}/campaigns").mock(return_value=_ok({"Campaigns": []}))
    out = await do_plan_write(
        _ctx(tmp_path),
        "adgroups_create",
        {
            "account": "t",
            "groups": [{"campaign_id": 2, "name": "Группа", "region_ids": [213]}],
        },
    )
    assert out.startswith("Ошибка подготовки")
    assert "не найдены" in out


# --- ads_create: дефолт, совместимость, display ---


def _text_item():
    return {
        "title": "Эмаль тест",
        "text": "t",
        "href": "https://example.com",
        "display_url_path": "test",
    }


def _resp_item():
    return {
        "titles": ["Эмаль тест"],
        "texts": ["t"],
        "href": "https://example.com",
        "display_url_path": "test",
    }


async def test_ads_default_by_content_text(respx_mock, tmp_path):
    _types(respx_mock)
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_create",
        {"account": "t", "adgroup_id": 5, "text_ads": [_text_item()]},
    )
    pid = _pid(out)
    _service, _method, body, version = split_request(PLANS.peek(pid).requests[0])
    assert version == "v5"
    assert "TextAd" in body["Ads"][0]


async def test_ads_default_by_content_responsive_unified(respx_mock, tmp_path):
    _types(respx_mock, group_type="UNIFIED_AD_GROUP", campaign_type="UNIFIED_CAMPAIGN")
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_create",
        {"account": "t", "adgroup_id": 5, "responsive_ads": [_resp_item()]},
    )
    pid = _pid(out)
    _service, _method, body, version = split_request(PLANS.peek(pid).requests[0])
    assert version == "v501"
    assert "ResponsiveAd" in body["Ads"][0]


async def test_ads_explicit_text_in_unified_warns_conversion(respx_mock, tmp_path):
    _types(respx_mock, group_type="UNIFIED_AD_GROUP", campaign_type="UNIFIED_CAMPAIGN")
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_create",
        {
            "account": "t",
            "adgroup_id": 5,
            "ad_type": "TEXT_AD",
            "text_ads": [_text_item()],
        },
    )
    assert "конвертирует в RESPONSIVE_AD" in out
    assert "acknowledge_warnings=true" in out


async def test_ads_dynamic_group_rejected(respx_mock, tmp_path):
    _types(
        respx_mock, group_type="DYNAMIC_TEXT_AD_GROUP", campaign_type="UNIFIED_CAMPAIGN"
    )
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_create",
        {"account": "t", "adgroup_id": 5, "responsive_ads": [_resp_item()]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "DYNAMIC_TEXT_AD_GROUP" in out


async def test_ads_missing_group_rejected(respx_mock, tmp_path):
    respx_mock.post(f"{BASE_V501}/adgroups").mock(return_value=_ok({"AdGroups": []}))
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_create",
        {"account": "t", "adgroup_id": 5, "responsive_ads": [_resp_item()]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "не найдена" in out


async def test_ads_responsive_display_required(respx_mock, tmp_path):
    _types(respx_mock, group_type="UNIFIED_AD_GROUP", campaign_type="UNIFIED_CAMPAIGN")
    item = _resp_item()
    del item["display_url_path"]
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_create",
        {"account": "t", "adgroup_id": 5, "responsive_ads": [item]},
    )
    assert out.startswith("Ошибка подготовки")
    assert "DisplayUrlPath" in out


# --- цепочка создания без модерации; moderate под guard ---


async def test_create_chain_has_no_moderate(respx_mock, tmp_path):
    _types(respx_mock)
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok(
            {"Campaigns": [{"Id": 2, "Name": "K", "Type": "TEXT_CAMPAIGN"}]}
        )
    )
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=_ok(
            {
                "AdGroups": [
                    {"Id": 5, "CampaignId": 2, "NegativeKeywords": {"Items": []}}
                ]
            }
        )
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_ok(
            {
                "Campaigns": [
                    {
                        "Id": 2,
                        "NegativeKeywords": {"Items": []},
                        "UnifiedCampaign": {"BiddingStrategy": {}},
                    }
                ]
            }
        )
    )
    respx_mock.post(f"{BASE}/keywords").mock(return_value=_ok({"Keywords": []}))
    plans = [
        await do_plan_write(
            _ctx(tmp_path),
            "campaigns_create",
            {"account": "t", "name": "[TEST DirectAI] Цепочка"},
        ),
        await do_plan_write(
            _ctx(tmp_path),
            "adgroups_create",
            {
                "account": "t",
                "groups": [{"campaign_id": 2, "name": "Г", "region_ids": [213]}],
            },
        ),
        await do_plan_write(
            _ctx(tmp_path),
            "keywords_add",
            {"account": "t", "adgroup_id": 5, "keywords": [{"text": "грунт эмаль"}]},
        ),
        await do_plan_write(
            _ctx(tmp_path),
            "ads_create",
            {"account": "t", "adgroup_id": 5, "text_ads": [_text_item()]},
        ),
    ]
    for out in plans:
        pid = _pid(out)
        for service, method, _body, *_rest in PLANS.peek(pid).requests:
            assert (service, method) != ("ads", "moderate")


async def test_moderate_blocked_by_default(tmp_path):
    out = await do_plan_write(
        _ctx(tmp_path),
        "ads_state",
        {"account": "t", "ad_ids": [9], "operation": "moderate"},
    )
    assert out.startswith("Заблокировано защитой")
    assert "модерация" in out.lower()
