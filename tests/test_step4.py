"""Step 4 tests: plans, warnings gate, guard bypasses, unverified (SPEC 9)."""

import httpx
import pytest

from directai_mcp.catalog.registry import Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety import journal as journal_mod
from directai_mcp.safety.plans import Plan, PlanStore
from directai_mcp.server import PLANS, do_apply_write, do_get_log, do_plan_write

BASE = "https://api.direct.yandex.com/json/v5"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=True,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _campaigns(items):
    return httpx.Response(200, json={"result": {"Campaigns": items}})


def _keywords(items):
    return httpx.Response(200, json={"result": {"Keywords": items}})


def test_store_rejects_used_and_expired():
    store = PlanStore()
    plan = Plan(
        plan_id="",
        action="a",
        account_login="l",
        params={},
        before=None,
        requests=[],
        preview="p",
    )
    pid = store.put(plan)
    assert store.take(pid) is not None
    assert store.take(pid) is None
    pid2 = store.put(
        Plan(
            plan_id="",
            action="a",
            account_login="l",
            params={},
            before=None,
            requests=[],
            preview="p",
        )
    )
    store._plans[pid2].created_at -= 10000.0
    assert store.take(pid2) is None


async def test_apply_unknown_plan_id(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_apply_write(ctx, "deadbeef1234")
    assert "неизвестен, просрочен или уже применён" in out


async def test_warnings_gate(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/keywords").mock(
        side_effect=[
            httpx.Response(200, json={"result": {"SuspendResults": [{}]}}),
            _keywords([{"Id": 7, "State": "SUSPENDED"}]),
        ]
    )
    plan = Plan(
        plan_id="",
        action="keywords_state",
        account_login="test-login",
        params={"account": "t", "keyword_ids": [7], "operation": "suspend"},
        before={7: "ON"},
        requests=[("keywords", "suspend", {"SelectionCriteria": {"Ids": [7]}})],
        preview="p",
        warnings=["w1"],
    )
    pid = PLANS.put(plan)
    out = await do_apply_write(ctx, pid, acknowledge_warnings=False)
    assert "acknowledge_warnings=true" in out
    out = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус applied" in out
    assert "Запись журнала #" in out
    assert "keywords_state" in do_get_log(ctx)


async def test_bypass_real_campaign_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 1, "Name": "ПОИСК - real"}])
    )
    out = await do_plan_write(
        ctx,
        "campaigns_state",
        {"account": "t", "campaign_ids": [1], "operation": "suspend"},
    )
    assert out.startswith("Заблокировано защитой")
    assert len(PLANS) == 0


async def test_bypass_rename_with_prefix_blocked(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx,
        "campaigns_update",
        {"account": "t", "campaign_ids": [1], "name": "[TEST DirectAI] hack"},
    )
    assert out.startswith("Заблокировано защитой")
    assert "переименование" in out.lower()
    assert len(PLANS) == 0


async def test_bypass_shared_set_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns(
            [
                {
                    "Id": 1,
                    "Name": "ПОИСК - real",
                    "TextCampaign": {"NegativeKeywordSharedSetIds": {"Items": [5]}},
                }
            ]
        )
    )
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=httpx.Response(200, json={"result": {"AdGroups": []}})
    )
    out = await do_plan_write(
        ctx,
        "negatives_set",
        {"account": "t", "update_shared_set": {"id": 5, "negatives": ["x"]}},
    )
    assert out.startswith("Заблокировано защитой")
    assert len(PLANS) == 0


async def test_bypass_moderate_blocked(tmp_path):
    ctx = _ctx(tmp_path)
    out = await do_plan_write(
        ctx, "ads_state", {"account": "t", "ad_ids": [9], "operation": "moderate"}
    )
    assert out.startswith("Заблокировано защитой")
    assert "модерация" in out.lower()
    assert len(PLANS) == 0


async def test_guard_allows_test_campaign(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns(
            [{"Id": 2, "Name": "[TEST DirectAI] Шаг 4", "State": "SUSPENDED"}]
        )
    )
    out = await do_plan_write(
        ctx,
        "campaigns_state",
        {"account": "t", "campaign_ids": [2], "operation": "resume"},
    )
    assert out.startswith("План ")
    assert "→ ON" in out


async def test_state_preconditions_by_actual_state(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns(
            [{"Id": 2, "Name": "[TEST DirectAI] Шаг 4", "State": "OFF"}]
        )
    )
    for operation in ("resume", "archive"):
        out = await do_plan_write(
            ctx,
            "campaigns_state",
            {"account": "t", "campaign_ids": [2], "operation": operation},
        )
        assert out.startswith("Ошибка подготовки"), out
        assert len(PLANS) == 0
    out = await do_plan_write(
        ctx,
        "campaigns_state",
        {"account": "t", "campaign_ids": [2], "operation": "unarchive"},
    )
    assert out.startswith("Ошибка подготовки")
    assert len(PLANS) == 0


def _text_ad():
    return {
        "Id": 12,
        "Type": "TEXT_AD",
        "AdGroupId": 5,
        "TextAd": {
            "Title": "Эмаль тест",
            "Text": "t",
            "Href": "https://example.com",
            "DisplayUrlPath": "test",
        },
    }


async def _plan_ads_update(ctx, respx_mock, ad, update_params):
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[
            httpx.Response(200, json={"result": {"Ads": [dict(ad)]}}),
            httpx.Response(200, json={"result": {"Ads": [dict(ad)]}}),
        ]
    )
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=httpx.Response(
            200, json={"result": {"AdGroups": [{"Id": 5, "CampaignId": 2}]}}
        )
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}])
    )
    return await do_plan_write(ctx, "ads_update", update_params)


async def test_update_text_ad_with_responsive_fields_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    out = await _plan_ads_update(
        ctx,
        respx_mock,
        _text_ad(),
        {"account": "t", "ad_ids": [12], "titles": ["Новый"],
         "display_url_path": "test"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "только для RESPONSIVE_AD" in out
    assert len(PLANS) == 0


async def test_update_responsive_ad_with_text_fields_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    out = await _plan_ads_update(
        ctx,
        respx_mock,
        _responsive_ad(),
        {"account": "t", "ad_ids": [11], "title": "Новый",
         "display_url_path": "test"},
    )
    assert out.startswith("Ошибка подготовки")
    assert "только для TEXT_AD" in out
    assert len(PLANS) == 0


async def test_update_unknown_ad_type_blocked(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    ad = {"Id": 13, "Type": "IMAGE_AD", "AdGroupId": 5}
    out = await _plan_ads_update(
        ctx, respx_mock, ad, {"account": "t", "ad_ids": [13], "title": "X",
                              "display_url_path": "test"}
    )
    assert out.startswith("Ошибка подготовки")
    assert "только TEXT_AD и RESPONSIVE_AD" in out
    assert len(PLANS) == 0


def _responsive_ad():
    return {
        "Id": 11,
        "Type": "RESPONSIVE_AD",
        "AdGroupId": 5,
        "ResponsiveAd": {
            "Titles": [{"Title": "Эмаль тест"}],
            "Texts": [{"Text": "t"}],
            "Href": "https://example.com",
        },
    }


async def test_responsive_ad_without_display_url_no_plan(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    ad = _responsive_ad()
    respx_mock.post(f"{BASE}/ads").mock(
        side_effect=[
            httpx.Response(200, json={"result": {"Ads": [dict(ad)]}}),
            httpx.Response(200, json={"result": {"Ads": [dict(ad)]}}),
        ]
    )
    respx_mock.post(f"{BASE}/adgroups").mock(
        return_value=httpx.Response(
            200, json={"result": {"AdGroups": [{"Id": 5, "CampaignId": 2}]}}
        )
    )
    respx_mock.post(f"{BASE}/campaigns").mock(
        return_value=_campaigns([{"Id": 2, "Name": "[TEST DirectAI] Шаг 4"}])
    )
    out = await do_plan_write(
        ctx, "ads_update", {"account": "t", "ad_ids": [11], "texts": ["Новый текст"]}
    )
    assert out.startswith("Ошибка подготовки")
    assert "DisplayUrlPath" in out
    assert len(PLANS) == 0


async def test_unverified_on_write_timeout(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{BASE}/campaigns").mock(
        side_effect=[
            _campaigns(
                [{"Id": 2, "Name": "[TEST DirectAI] Шаг 4", "State": "SUSPENDED"}]
            ),
            _campaigns(
                [{"Id": 2, "Name": "[TEST DirectAI] Шаг 4", "State": "SUSPENDED"}]
            ),
            httpx.TimeoutException("boom"),
            _campaigns([{"Id": 2, "State": "SUSPENDED"}]),
        ]
    )
    plan_out = await do_plan_write(
        ctx,
        "campaigns_state",
        {"account": "t", "campaign_ids": [2], "operation": "resume"},
    )
    pid = plan_out.split()[1].rstrip(":")
    out = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус unverified" in out
    conn = journal_mod.connect(tmp_path)
    try:
        rows = journal_mod.recent(conn)
    finally:
        conn.close()
    assert rows and rows[0]["status"] == "unverified"
