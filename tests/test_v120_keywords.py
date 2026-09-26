"""v1.1.10: полные фразы в keywords, AdGroupName, подпись, флаг только placements."""

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"

CHUNK = (
    "CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
    "CriterionType\tImpressions\tClicks\tCost\n"
    "7\t1\tГруппа А\tгрунт эмаль 3 в 1 -ржавчина -цена\t11\tKEYWORD\t"
    "393\t85\t500.00\n"
    "7\t2\tГруппа Б\tгрунт эмаль 3 в 1 -краска\t22\tKEYWORD\t"
    "100\t5\t100.00\n"
)


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


async def test_keywords_full_phrase_group_and_note(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(return_value=_tsv(CHUNK))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "AdGroupName" in sent["FieldNames"]
    # v1.1.20: split-режим — MatchType в запросе, строки не склеиваются.
    assert "MatchType" in sent["FieldNames"]
    # Полные фразы с минусами — кросс-минусованные различимы.
    assert "грунт эмаль 3 в 1 -ржавчина -цена" in out
    assert "грунт эмаль 3 в 1 -краска" in out
    # v1.1.20: чистая фраза — только CSV/JSON, в inline/MD её нет
    # (агенты брали короткую и теряли минусы).
    assert "CleanCriterion" not in out
    assert "| грунт эмаль 3 в 1 -ржавчина -цена | 11 |" in out
    # Группа видна, сверка с итогом кампании по числам (v1.1.26).
    assert "Группа А" in out and "Группа Б" in out
    assert "расхождение в копейках" not in out
    assert "Сверка с итогом кампании: сходится (493 / 90 / 600.00 ₽)." in out


async def test_keywords_clean_criterion_file_only(respx_mock, tmp_path):
    route = respx_mock.post(RURL).mock(return_value=_tsv(CHUNK))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False,
            output="file", format="csv"))
    sent = route.calls[0].request.content.decode()
    assert "MatchType" in sent
    path = out.split("Полный результат: ")[1].split(" (")[0]
    from pathlib import Path as _P

    text = _P(path).read_text(encoding="utf-8-sig")
    assert "CleanCriterion" in text.splitlines()[0]
    assert "грунт эмаль 3 в 1" in text


async def test_no_anomaly_flag_in_keywords(respx_mock, tmp_path):
    """CTR 21.63% при 85 кликах — флага нет: он только для площадок."""
    respx_mock.post(RURL).mock(return_value=_tsv(CHUNK))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    assert "| 21.63% |" in out
    assert "Flag" not in out
    assert "аномальный" not in out


async def test_no_anomaly_flag_in_queries(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(side_effect=[
        _tsv("CampaignId\tAdGroupId\tQuery\tMatchedKeyword\tCriterion\t"
             "Impressions\tClicks\tCost\n"
             "7\t1\tзапрос\t\tфраза\t393\t85\t500.00\n"),
        _tsv("CampaignId\tImpressions\tClicks\tCost\n7\t393\t85\t500.00\n"),
    ])
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_search_queries"].run(
        ctx, ACTIONS["stats_search_queries"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False,
            query_grouping="raw"))
    assert "Flag" not in out
    assert "аномальный" not in out
