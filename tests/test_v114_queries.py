"""v1.1.4: группировка запросов, покрытие из агрегата, НДС, обрезки."""

import json

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.ads import _cut
from directai_mcp.catalog.campaigns import _join
from directai_mcp.catalog.common import clean_phrase
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import SearchQueriesParams, group_query_rows
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
HDR = "CampaignId\tAdGroupId\tQuery\tMatchedKeyword\tCriterion\tImpressions\tClicks\tCost"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def test_group_sums_and_recomputes():
    cols = ["CampaignId", "AdGroupId", "Query", "MatchedKeyword", "Criterion",
            "Impressions", "Clicks", "Cost"]
    rows = [
        {"CampaignId": "7", "AdGroupId": "1", "Query": "краска",
         "MatchedKeyword": "", "Criterion": "краска",
         "Impressions": "142", "Clicks": "3", "Cost": "592.31"},
        {"CampaignId": "7", "AdGroupId": "1", "Query": "краска",
         "MatchedKeyword": "", "Criterion": "другая фраза",
         "Impressions": "1", "Clicks": "0", "Cost": "0.00"},
    ]
    grouped, n_raw = group_query_rows(cols, rows)
    assert n_raw == 2 and len(grouped) == 1
    row = grouped[0]
    assert row["Impressions"] == "143"
    assert row["Clicks"] == "3"
    assert row["Cost"] == "592.31"
    assert row["Criterion"] == "2 значения"


def test_group_goal_columns():
    cols = ["Query", "Conversions_1_AUTO", "Revenue_1_AUTO",
            "CostPerConversion_1_AUTO"]
    rows = [
        {"Query": "q", "Conversions_1_AUTO": "1", "Revenue_1_AUTO": "200.00",
         "CostPerConversion_1_AUTO": "100.00"},
        {"Query": "q", "Conversions_1_AUTO": "1", "Revenue_1_AUTO": "200.00",
         "CostPerConversion_1_AUTO": "100.00"},
    ]
    grouped, _ = group_query_rows(cols, rows)
    row = grouped[0]
    assert row["Conversions_1_AUTO"] == "2"
    assert row["Revenue_1_AUTO"] == "400"
    assert row["CostPerConversion_1_AUTO"] == "200"


def _tsv(body):
    return httpx.Response(200, text=body)


async def test_query_vs_raw_mode(respx_mock, tmp_path):
    rows_tsv = ("CampaignId\tAdGroupId\tQuery\tMatchedKeyword\tCriterion\t"
                "Impressions\tClicks\tCost\n"
                "7\t1\tкраска\t\tфраза а\t10\t1\t100.00\n"
                "7\t1\tкраска\t\tфраза б\t5\t2\t50.00\n")
    agg_tsv = "CampaignId\tImpressions\tClicks\tCost\n7\t20\t3\t150.00\n"
    respx_mock.post(RURL).mock(side_effect=[_tsv(rows_tsv), _tsv(agg_tsv),
                                            _tsv(rows_tsv), _tsv(agg_tsv)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_search_queries"]
    assert act.params is SearchQueriesParams
    grouped = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                            with_conversions=False))
    assert "2 сырых строк → 1 запросов" in grouped
    assert "покрывает 15 из 20 показов; клики 3 из 3" in grouped
    ctx2 = _ctx(tmp_path)
    raw_out = await act.run(ctx2, act.params(account="agency-login", campaign_ids=[7],
                                             with_conversions=False,
                                             query_grouping="raw"))
    assert "сгруппированы" not in raw_out
    assert "фраза а" in raw_out and "фраза б" in raw_out


def test_vat_label(tmp_path):
    from directai_mcp.catalog.stats import _context

    ctx = _ctx(tmp_path)
    entries = list(ctx.settings.accounts.values())
    params = ACTIONS["stats_campaigns"].params(period="YESTERDAY")
    key = _context(ctx, "stats_campaigns", entries, params, None)
    assert "расход с НДС" in key


def test_truncation_code_point_safe():
    word = "минус-фраза"
    assert _cut(word * 30, 10).endswith("…")
    assert _join([word * 30], 10).endswith("…")
    for text in (_cut(word * 30, 10), _join([word * 30], 10),
                 clean_phrase("минус слово - хлам", False)):
        text.encode("utf-8")
        assert "\ufffd" not in text


async def test_aggregate_rows_not_collapsed(respx_mock, tmp_path):
    chunk_tsv = "CampaignId\tConversions_1_AUTO\n7\t4\n"
    agg_tsv = ("CampaignId\tImpressions\tClicks\tCost\n"
               "7\t100\t5\t1100.00\n7\t60\t3\t500.00\n")
    respx_mock.post(RURL).mock(side_effect=[_tsv(chunk_tsv), _tsv(agg_tsv)])
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_campaigns"]
    out = await act.run(ctx, act.params(account="agency-login", campaign_ids=[7],
                                        goals=["1"], attribution=["AUTO"],
                                        with_conversions=True))
    assert "Показы: 160" in out
    assert "Клики: 8" in out
    assert "Расход: 1 600.00 ₽" in out


async def test_custom_aggregate_without_dims(respx_mock, tmp_path):
    """v1.1.6: итог custom с group_by — из агрегата без измерений."""
    per_device = "Device\tClicks\tCost\nDESKTOP\t10\t100.06\nMOBILE\t5\t50.01\n"
    agg = "Clicks\tCost\n15\t150.08\n"
    route = respx_mock.post(RURL).mock(
        side_effect=[_tsv(per_device), _tsv(agg)]
    )
    ctx = _ctx(tmp_path)
    act = ACTIONS["stats_custom"]
    out = await act.run(ctx, act.params(account="agency-login",
                                        report_type="CUSTOM_REPORT",
                                        field_names=["Clicks", "Cost"],
                                        group_by=["Device"],
                                        with_conversions=False))
    assert route.call_count == 2
    agg_sent = json.loads(route.calls[1].request.content)["params"]
    assert agg_sent["FieldNames"] == ["Clicks", "Cost"]
    assert "Расход: 150.08 ₽" in out
