"""v1.1.7: ранг #, подпись итога разрезов, единые цели key, фильтр пустых."""

import httpx
import pytest

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import (
    add_rank,
    drop_empty_rows,
    is_empty_row,
)
from directai_mcp.config import AccountEntry, Settings

V501 = "https://api.direct.yandex.com/json/v501/campaigns"
RURL = "https://api.direct.yandex.com/json/v5/reports"

GOAL_CAMP = {"Campaigns": [{
    "Id": 7, "Type": "UNIFIED_CAMPAIGN",
    "UnifiedCampaign": {
        "PriorityGoals": {"Items": [{"GoalId": 9}]},
        "BiddingStrategy": {},
    },
}]}


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _tsv(body):
    return httpx.Response(200, text=body)


def test_is_empty_row_rules():
    assert is_empty_row({})  # всё None/отсутствует — пустая
    assert is_empty_row({"Impressions": "0", "Clicks": "0", "Cost": "0.00",
                         "Conversions_9_AUTO": "0"})
    assert is_empty_row({"Impressions": None, "Clicks": None, "Cost": None})
    # Показы без кликов — не пустая (сигнал для CTR).
    assert not is_empty_row({"Impressions": "50", "Clicks": "0", "Cost": "0.00"})
    assert not is_empty_row({"Impressions": "0", "Clicks": "0", "Cost": "0.00",
                             "Conversions": "1"})
    assert not is_empty_row({"Impressions": "0", "Clicks": "0", "Cost": "0.00",
                             "Conversions_9_AUTO": "2"})
    assert not is_empty_row({"Impressions": "10", "Clicks": "1", "Cost": "5.00"})


def test_drop_empty_rows_counts():
    rows = [{"Impressions": "1"}, {"Impressions": "0", "Cost": "0.00"}]
    kept, n = drop_empty_rows(rows)
    assert n == 1 and len(kept) == 1
    assert kept[0] == {"Impressions": "1"}


def test_add_rank_sequential():
    rows = [{"Cost": "5"}, {"Cost": "1"}]
    add_rank(rows)
    assert [r["#"] for r in rows] == [1, 2]


REGION_CHUNK = (
    "LocationOfPresenceId\tLocationOfPresenceName\tImpressions\tClicks\tCost\t"
    "Conversions_9_AUTO\n"
    "213\tМосква\t100\t5\t1100.00\t2\n"
    "2\tСПб\t50\t0\t0.00\t0\n"
    "1\tПусто\t0\t0\t0.00\t0\n"
)
REGION_AGG = "LocationOfPresenceId\tImpressions\tClicks\tCost\n213\t150\t5\t1100.00\n"


REGION_RECONC = "CampaignId\tImpressions\tClicks\tCost\n7\t150\t5\t1100.00\n"


async def test_regions_rank_filter_note(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv(REGION_CHUNK), _tsv(REGION_AGG), _tsv(REGION_RECONC)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_regions"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                       goals=["9"], attribution=["AUTO"]))
    assert "| # | LocationOfPresenceId |" in out
    assert "| 1 | 213 | Москва |" in out
    assert "| 2 | 2 | СПб |" in out  # показы без кликов оставлены
    assert "Пусто" not in out.split("Пустых строк исключено")[0]
    assert "Пустых строк исключено: 1" in out
    assert "include_empty=true" in out
    # v1.1.26: вместо оговорки про копейки — сверка по числам.
    assert "расхождение в копейках" not in out
    assert "Сверка с итогом кампании: сходится (150 / 5 / 1 100.00 ₽)." in out


async def test_regions_include_empty_keeps(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv(REGION_CHUNK), _tsv(REGION_AGG), _tsv(REGION_RECONC)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_regions"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                       goals=["9"], attribution=["AUTO"],
                                       include_empty=True))
    assert "| 3 | 1 | Пусто |" in out
    assert "Пустые строки оставлены: 1" in out


async def test_campaigns_and_summary_without_breakdown_note(respx_mock, tmp_path):
    chunk = "CampaignId\tCampaignName\tImpressions\tClicks\tCost\n7\tК\t10\t1\t100.00\n"
    respx_mock.post(RURL).mock(return_value=_tsv(chunk))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_campaigns"].run(
        ctx, ACTIONS["stats_campaigns"].params(account="agency-login",
                                              with_conversions=False))
    assert "| # | CampaignId |" in out
    assert "расхождение в копейках" not in out
    assert "Сверка с итогом кампании" not in out
    ctx2 = _ctx(tmp_path)
    out2 = await ACTIONS["stats_summary"].run(
        ctx2, ACTIONS["stats_summary"].params(account="agency-login",
                                             with_conversions=False))
    assert "расхождение в копейках" not in out2
    assert "Сверка с итогом кампании" not in out2


# Дефолт key для всех stats_*: одинаковые Goals в запросе при живых целях [9].
TOOL_CASES = {
    "stats_summary": ({}, [], "Impressions\tClicks\tCost"),
    "stats_campaigns": ({}, ["CampaignId", "CampaignName"], None),
    "stats_adgroups": ({}, ["CampaignId", "CampaignName", "AdGroupId", "AdGroupName"], None),
    "stats_ads": ({}, ["CampaignId", "CampaignName", "AdGroupId", "AdGroupName", "AdId"], None),
    "stats_keywords": ({}, ["CampaignId", "AdGroupId", "Criterion", "CriterionId",
                            "CriterionType"], None),
    "stats_audiences": ({}, ["CampaignId", "AdGroupId", "Criterion", "CriterionId",
                             "CriterionType"], None),
    "stats_search_queries": ({"query_grouping": "query"},
                             ["CampaignId", "AdGroupId", "Query", "MatchedKeyword",
                              "Criterion"], None),
    "stats_regions": ({}, ["LocationOfPresenceId", "LocationOfPresenceName"], None),
    "stats_placements": ({}, ["Placement"], None),
    "stats_devices": ({}, ["Device"], None),
}


def _chunk_tsv(dims):
    header = "\t".join(dims + ["Impressions", "Clicks", "Cost", "Conversions_9_AUTO"])
    if dims:
        row = "\t".join(["d"] * len(dims) + ["100", "5", "1100.00", "2"])
    else:
        row = "100\t5\t1100.00\t2"
    return header + "\n" + row + "\n"


def _agg_tsv(dims, name):
    agg_dims = ["CampaignId"] if name == "stats_search_queries" else dims
    header = "\t".join(agg_dims + ["Impressions", "Clicks", "Cost"]) if agg_dims else (
        "Impressions\tClicks\tCost")
    if agg_dims:
        row = "\t".join(["d"] * len(agg_dims) + ["100", "5", "1100.00"])
    else:
        row = "100\t5\t1100.00"
    return header + "\n" + row + "\n"


@pytest.mark.parametrize("name", sorted(TOOL_CASES))
async def test_unified_key_goals_default(respx_mock, tmp_path, name):
    extra, dims, _ = TOOL_CASES[name]
    route = respx_mock.post(RURL).mock(
        side_effect=[_tsv(_chunk_tsv(dims)), _tsv(_agg_tsv(dims, name))])
    respx_mock.post(V501).mock(return_value=_ok(GOAL_CAMP))
    ctx = _ctx(tmp_path)
    act = ACTIONS[name]
    out = await act.run(ctx, act.params(account="agency-login", **extra))
    import json as _json

    sent = _json.loads(route.calls[0].request.content)["params"]
    assert sent["Goals"] == ["9"]
    assert sent["AttributionModels"] == ["AUTO"]
    assert "режим: key" in out
    assert "| # |" in out
    # v1.1.26: сверка reuse'ит агрегат поискового отчёта (уровень кампании,
    # доп. запроса нет) — здесь тоже сходится.
    if name == "stats_search_queries":
        assert "Сверка с итогом кампании: сходится (100 / 5 / 1 100.00 ₽)." in out
    else:
        assert "Сверка с итогом кампании" not in out


async def test_custom_unified_goals_and_note(respx_mock, tmp_path):
    route = respx_mock.post(RURL).mock(side_effect=[
        _tsv("Device\tClicks\tCost\tConversions_9_AUTO\nDESKTOP\t10\t100.00\t1\n"),
        _tsv("Clicks\tCost\n10\t100.00\n"),
    ])
    respx_mock.post(V501).mock(return_value=_ok(GOAL_CAMP))
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_custom"]
    import json as _json

    out = await act.run(ctx, act.params(account="agency-login",
                                       report_type="CUSTOM_REPORT",
                                       field_names=["Clicks", "Cost"],
                                       group_by=["Device"]))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert sent["Goals"] == ["9"]
    assert "расхождение в копейках" not in out
    assert "Сверка с итогом кампании" not in out
    assert "| # |" in out


async def test_custom_no_sniffing_without_conversions(respx_mock, tmp_path):
    """v1.1.7: with_conversions=false не тянет авто-цели даже при поле Conversions."""
    route = respx_mock.post(RURL).mock(
        return_value=_tsv("Clicks\tConversions\n5\t3\n"))
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_custom"]
    import json as _json

    await act.run(ctx, act.params(account="agency-login",
                                 report_type="CUSTOM_REPORT",
                                 field_names=["Clicks", "Conversions"],
                                 with_conversions=False))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "Goals" not in sent
    assert route.call_count == 1  # без целей агрегат не нужен


async def test_custom_campaign_nodims_without_note(respx_mock, tmp_path):
    route = respx_mock.post(RURL).mock(side_effect=[
        _tsv("Clicks\tCost\tConversions_9_AUTO\n5\t100.00\t1\n"),
        _tsv("Clicks\tCost\n5\t100.00\n"),
    ])
    respx_mock.post(V501).mock(return_value=_ok(GOAL_CAMP))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_custom"].run(
        ctx, ACTIONS["stats_custom"].params(
            account="agency-login", report_type="CAMPAIGN_PERFORMANCE_REPORT",
            field_names=["Clicks", "Cost"]))
    assert route.call_count == 2
    assert "расхождение в копейках" not in out
