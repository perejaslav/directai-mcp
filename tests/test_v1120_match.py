"""v1.1.20: MatchType split/sum, CleanCriterion file-only, настройки автотаргетинга."""

import httpx

import directai_mcp.catalog.adgroups as _g  # noqa: F401 (реестр)
import directai_mcp.catalog.keywords as _k  # noqa: F401 (реестр)
import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.keywords import (
    autotargeting_by_group,
    autotargeting_summary,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
BASE = "https://api.direct.yandex.com/json/v5"
V501G = "https://api.direct.yandex.com/json/v501/adgroups"

SPLIT = (
    "CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
    "CriterionType\tMatchType\tImpressions\tClicks\tCost\n"
    "7\t1\tГруппа А\tэмаль грунт по ржавчине 3в1 -купить\t11\tKEYWORD\t"
    "SYNONYM\t165\t12\t1861.79\n"
    "7\t1\tГруппа А\tэмаль грунт по ржавчине 3в1 -купить\t11\tKEYWORD\t"
    "KEYWORD\t47\t3\t913.51\n"
)
SUMMED = (
    "CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
    "CriterionType\tImpressions\tClicks\tCost\n"
    "7\t1\tГруппа А\tэмаль грунт по ржавчине 3в1 -купить\t11\tKEYWORD\t"
    "212\t15\t2775.30\n"
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


def test_autotargeting_summary_etalon():
    settings = {"Categories": {"Exact": "YES", "Narrow": "NO",
                               "Alternative": "NO", "Accessory": "NO",
                               "Broader": "NO"},
                "BrandOptions": {"WithoutBrands": "NO",
                                 "WithAdvertiserBrand": "YES",
                                 "WithCompetitorsBrand": "NO"}}
    assert autotargeting_summary(settings) == "целевые; бренд рекламодателя"
    assert autotargeting_summary(None) is None
    assert autotargeting_summary({}) is None


async def test_match_split_no_glue(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(return_value=_tsv(SPLIT))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "MatchType" in sent["FieldNames"]
    assert "| CriterionType | MatchType | Impressions |" in out
    assert "| SYNONYM | 165 | 12 |" in out
    assert "1 861.79" in out
    assert "| KEYWORD | 47 | 3 |" in out
    assert "913.51" in out


async def test_match_sum_merged_by_api(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(return_value=_tsv(SUMMED))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False,
            match_mode="sum"))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "MatchType" not in sent["FieldNames"]
    assert "MatchType" not in out
    assert "| 212 | 15 |" in out and "2 775.30" in out


async def test_keywords_list_autotargeting(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(f"{BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": [
            {"Id": 1, "AdGroupId": 5, "Keyword": "фраза",
             "State": "ON", "Status": "ACCEPTED", "ServingStatus": "ELIGIBLE",
             "Bid": 9000001, "ContextBid": 500000},
            {"Id": 2, "AdGroupId": 5, "Keyword": "---autotargeting",
             "State": "ON", "Status": "ACCEPTED", "ServingStatus": "ELIGIBLE",
             "Bid": None, "ContextBid": None,
             "AutotargetingSettings": {
                 "Categories": {"Exact": "YES", "Narrow": "NO",
                                "Alternative": "NO", "Accessory": "NO",
                                "Broader": "NO"},
                 "BrandOptions": {"WithoutBrands": "NO",
                                  "WithAdvertiserBrand": "YES",
                                  "WithCompetitorsBrand": "NO"}}},
        ]}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["keywords_list"].run(
        ctx, ACTIONS["keywords_list"].params(account="m", adgroup_ids=[5]))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "AutotargetingSettingsCategoriesFieldNames" in sent
    assert "AutotargetingSettingsBrandOptionsFieldNames" in sent
    assert "| ContextBid | Autotargeting |" in out
    assert "целевые; бренд рекламодателя" in out


async def test_adgroups_list_autotargeting(respx_mock, tmp_path):
    respx_mock.post(V501G).mock(
        return_value=httpx.Response(200, json={"result": {"AdGroups": [
            {"Id": 5, "CampaignId": 7, "Name": "Группа",
             "Type": "TEXT_AD_GROUP", "Status": "ACCEPTED",
             "ServingStatus": "ELIGIBLE", "RegionIds": [213],
             "RestrictedRegionIds": None},
        ]}}))
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": [
            {"Id": 2, "AdGroupId": 5, "Keyword": "---autotargeting",
             "AutotargetingSettings": {
                 "Categories": {"Exact": "YES", "Narrow": "NO",
                                "Alternative": "NO", "Accessory": "NO",
                                "Broader": "NO"},
                 "BrandOptions": {"WithoutBrands": "NO",
                                  "WithAdvertiserBrand": "YES",
                                  "WithCompetitorsBrand": "NO"}}},
        ]}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["adgroups_list"].run(
        ctx, ACTIONS["adgroups_list"].params(account="m", campaign_ids=[7]))
    assert "| Restricted | Autotargeting |" in out
    assert "целевые; бренд рекламодателя" in out


async def test_stats_keywords_autotargeting_column(respx_mock, tmp_path):
    chunk = ("CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
             "CriterionType\tImpressions\tClicks\tCost\n"
             "7\t5\tГруппа\t---autotargeting\t99\tAUTOTARGETING\t"
             "10\t1\t100.00\n")
    respx_mock.post(RURL).mock(return_value=_tsv(chunk))
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": [
            {"Id": 99, "AdGroupId": 5, "Keyword": "---autotargeting",
             "AutotargetingSettings": {
                 "Categories": {"Exact": "YES", "Narrow": "NO",
                                "Alternative": "NO", "Accessory": "NO",
                                "Broader": "NO"},
                 "BrandOptions": {"WithoutBrands": "NO",
                                  "WithAdvertiserBrand": "YES",
                                  "WithCompetitorsBrand": "NO"}}},
        ]}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_keywords"].run(
        ctx, ACTIONS["stats_keywords"].params(
            account="agency-login", campaign_ids=[7], with_conversions=False))
    assert "| CriterionType | Autotargeting |" in out
    assert "целевые; бренд рекламодателя" in out


async def test_autotargeting_by_group_maps_and_defaults(respx_mock, tmp_path):
    from directai_mcp.api.direct import DirectClient

    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": [
            {"Id": 2, "AdGroupId": 5, "Keyword": "---autotargeting",
             "AutotargetingSettings": {"Categories": {"Exact": "YES"}}},
            {"Id": 3, "AdGroupId": 5, "Keyword": "фраза"},
        ]}}))
    client = DirectClient(token="t")
    try:
        out = await autotargeting_by_group(client, "x", [5, 6])
    finally:
        await client.aclose()
    assert out[5] == "целевые"
    assert out[6] is None
