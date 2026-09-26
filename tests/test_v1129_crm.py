"""v1.1.29 (тест 24): типы ценности целей — CRM-выручка vs условная."""

from decimal import Decimal

import httpx
import pytest

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.metrika_goals import (
    COUNTER_GOALS_CACHE,
    metrika_type_to_value,
    resolve_value_types,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import (
    goal_ids_with_revenue,
    goal_value_lines,
    revenue_header_map,
    revenue_total_label,
)
from directai_mcp.config import (
    AccountEntry,
    ConfigError,
    Settings,
    _load_goals_file,
)

RURL = "https://api.direct.yandex.com/json/v5/reports"
V501C = "https://api.direct.yandex.com/json/v501/campaigns"


def _ctx(tmp_path, **kw):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        **kw,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def _ok(result):
    return httpx.Response(200, json={"result": result})


# --- п.1: value_type в goals.toml ---

def test_value_type_parsing(tmp_path):
    path = tmp_path / "goals.toml"
    path.write_text(
        "[goals]\n"
        '"1" = "Простая"\n'
        '[goals."2"]\nname = "CRM"\nvalue_type = "crm"\n'
        '[goals."3"]\nname = "Явная"\nvalue_type = "conditional"\n',
        encoding="utf-8",
    )
    names, counters, types = _load_goals_file(path)
    assert names == {"1": "Простая", "2": "CRM", "3": "Явная"}
    assert counters == {}
    assert types == {"1": "conditional", "2": "crm", "3": "conditional"}


def test_value_type_bad(tmp_path):
    path = tmp_path / "goals.toml"
    path.write_text('[goals."2"]\nvalue_type = "money"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="bad value_type"):
        _load_goals_file(path)


def test_metrika_type_mapping():
    assert metrika_type_to_value("cdp_order_in_progress") == "crm"
    assert metrika_type_to_value("cdp_order_paid") == "crm"
    assert metrika_type_to_value("action") == "conditional"
    assert metrika_type_to_value("") == "conditional"


# --- п.1: resolve (Метрика + fallback) ---

async def test_resolve_metrika_and_cache(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "TextCampaign": {"CounterIds": {"Items": [99]}}}]}))
    COUNTER_GOALS_CACHE[99] = {"1": "cdp_order_paid", "2": "action"}
    try:
        ctx = _ctx(tmp_path)
        types, source = await resolve_value_types(
            ctx, [AccountEntry(alias="m", login="agency-login")], [7], ["1", "2"])
        assert types == {"1": "crm", "2": "conditional"}
        assert source == "metrika"
        # Кеш: повтор без сети (роут сносим).
        respx_mock.reset()
        types2, _ = await resolve_value_types(
            ctx, [AccountEntry(alias="m", login="agency-login")], [7], ["1"])
        assert types2 == {"1": "crm"}
    finally:
        COUNTER_GOALS_CACHE.pop(99, None)


async def test_resolve_fallback_toml(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(
        return_value=httpx.Response(200, json={"error": {"code": 500}}))
    ctx = _ctx(tmp_path, goal_value_types={"5": "crm"})
    types, source = await resolve_value_types(
        ctx, [AccountEntry(alias="m", login="agency-login")], [7], ["5", "6"])
    assert types == {"5": "crm", "6": "conditional"}
    assert source == "goals.toml"


# --- п.2/п.3: вывод ---

def test_goal_value_lines_crm_drr_roi():
    cols = ["Conversions_9_AUTO", "Revenue_9_AUTO"]
    rows = [{"Conversions_9_AUTO": "2", "Revenue_9_AUTO": "3162925.20"}]
    lines = goal_value_lines(cols, rows, {"9": "Заказ создан"}, None,
                             {"9": "crm"}, Decimal("29991.64"))
    assert lines == [(
        "Выручка CRM цели Заказ создан (9): 1 581 462.60 ₽ · "
        "Суммарная: 3 162 925.20 ₽ (2 конв.) · ДРР 0.95% · ROI 10 446.02%"
    )]


def test_goal_value_lines_mixed_labels():
    cols = ["Conversions_9_AUTO", "Revenue_9_AUTO",
            "Conversions_1_AUTO", "Revenue_1_AUTO"]
    rows = [{"Conversions_9_AUTO": "2", "Revenue_9_AUTO": "100.00",
             "Conversions_1_AUTO": "1", "Revenue_1_AUTO": "50.00"}]
    lines = goal_value_lines(cols, rows, {}, None,
                             {"1": "conditional", "9": "crm"}, Decimal(10))
    assert lines[0].startswith("Ценность цели 1:")
    assert lines[1].startswith("Выручка CRM цели 9:")


def test_revenue_total_label():
    assert revenue_total_label({"1": "conditional"}) == "Ценность целей (условная)"
    assert revenue_total_label({"9": "crm"}) == "Выручка CRM"
    assert revenue_total_label({"1": "conditional", "9": "crm"}) is None
    assert revenue_total_label({}) == "Ценность целей (условная)"


def test_revenue_header_map_crm():
    assert revenue_header_map(["Revenue_9_AUTO"], {"9": "crm"}) == \
        {"Revenue_9_AUTO": "Выручка CRM_9_AUTO"}
    assert revenue_header_map(["Revenue_1_AUTO"], {"1": "conditional"}) == \
        {"Revenue_1_AUTO": "Ценность целей (условная)_1_AUTO"}
    assert revenue_header_map(["Cost"], {}) == {}


def test_goal_ids_with_revenue():
    assert goal_ids_with_revenue(["Revenue_9_AUTO", "Cost"]) == ["9"]
    assert goal_ids_with_revenue(["Cost"]) == []


# --- полный прогон: смешанные цели, итог без агрегата Revenue ---

DETAIL = (
    "CampaignId\tCampaignName\tImpressions\tClicks\tCost\t"
    "Conversions_9_AUTO\tRevenue_9_AUTO\tConversions_1_AUTO\tRevenue_1_AUTO\n"
    "7\tК\t100\t10\t29991.64\t2\t3162925.20\t5\t100.00\n"
)
AGG = ("Impressions\tClicks\tCost\tConversions\tRevenue\n"
       "100\t10\t29991.64\t3\t3194145.20\n")
RECONC = "CampaignId\tImpressions\tClicks\tCost\n7\t100\t10\t29991.64\n"


async def test_mixed_goals_no_revenue_total(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(DETAIL), _tsv(AGG), _tsv(RECONC)])
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "TextCampaign": {"CounterIds": {"Items": [99]}}}]}))
    COUNTER_GOALS_CACHE[99] = {"9": "cdp_order_paid", "1": "action"}
    try:
        ctx = _ctx(tmp_path)
        out = await ACTIONS["stats_campaigns"].run(
            ctx, ACTIONS["stats_campaigns"].params(
                account="agency-login", campaign_ids=[7], goals=["9", "1"],
                attribution=["AUTO"]))
        assert "Выручка CRM цели 9" in out
        assert "ДРР 0.95%" in out
        assert "Ценность цели 1:" in out
        assert "Ценность целей (условная):" not in out
        assert "Выручка CRM:" not in out.split("Итого:")[1].split(".")[0]
        assert "типы: metrika" in out
    finally:
        COUNTER_GOALS_CACHE.pop(99, None)


async def test_crm_only_revenue_total_label(respx_mock, tmp_path):
    detail = (
        "CampaignId\tCampaignName\tImpressions\tClicks\tCost\t"
        "Conversions_9_AUTO\tRevenue_9_AUTO\t"
        "Conversions_8_AUTO\tRevenue_8_AUTO\n"
        "7\tК\t100\t10\t29991.64\t2\t3162925.20\t1\t30220.00\n")
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(detail), _tsv(AGG), _tsv(RECONC)])
    respx_mock.post(V501C).mock(return_value=_ok({
        "Campaigns": [{"Id": 7, "TextCampaign": {"CounterIds": {"Items": [99]}}}]}))
    COUNTER_GOALS_CACHE[99] = {"9": "cdp_order_paid", "8": "cdp_order_paid"}
    try:
        ctx = _ctx(tmp_path)
        out = await ACTIONS["stats_campaigns"].run(
            ctx, ACTIONS["stats_campaigns"].params(
                account="agency-login", campaign_ids=[7], goals=["9", "8"],
                attribution=["AUTO"]))
        assert "Выручка CRM: 3 194 145.20 ₽" in out
        assert "Ценность целей (условная)" not in out
    finally:
        COUNTER_GOALS_CACHE.pop(99, None)
