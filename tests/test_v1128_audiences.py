"""v1.1.28 (тест 23): аудитории — Rules списков, агрегат CriterionType,
строки условий RETARGETING с маппингом через AudienceTargets."""

from decimal import Decimal

import httpx

import directai_mcp.catalog.audiences as _a  # noqa: F401 (реестр)
import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.audiences import _available_in, _rules_text
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.catalog.stats import audience_aggregate, criterion_summary
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"
BASE = "https://api.direct.yandex.com/json/v5"


def _ctx(tmp_path, goal_names=None):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        goal_names=goal_names or {},
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def _ok(result):
    return httpx.Response(200, json={"result": result})


# --- п.1: Rules ---

def test_rules_text_with_goal_names():
    rules = [{"Operator": "ANY",
              "Arguments": [{"ExternalId": 9000000002,
                             "MembershipLifeSpan": 540}]}]
    text = _rules_text(rules, {"9000000002": "Оставленная корзина"})
    assert text == "ANY: Оставленная корзина (9000000002) (540 дн.)"


def test_rules_text_many_args_no_names():
    rules = [{"Operator": "ANY",
              "Arguments": [{"ExternalId": 1, "MembershipLifeSpan": 0},
                            {"ExternalId": 2, "MembershipLifeSpan": 0}]}]
    text = _rules_text(rules, {})
    assert text == "ANY: 1 (0 дн.); 2 (0 дн.)"


def test_rules_text_empty():
    assert _rules_text(None, {}) == "—"
    assert _rules_text([], {}) == "—"
    assert _available_in(None) == "—"
    assert _available_in({"Items": ["TEXT_AD_GROUP", "MAX_ADS_AD_GROUP"]}) == \
        "TEXT_AD_GROUP, MAX_ADS_AD_GROUP"


async def test_audiences_list_rules_columns(respx_mock, tmp_path):
    respx_mock.post(f"{BASE}/audiencetargets").mock(return_value=_ok({
        "AudienceTargets": [
            {"Id": 90000013, "CampaignId": 900000002,
             "AdGroupId": 9000000006, "RetargetingListId": 90000002,
             "InterestId": None, "State": "ON", "ContextBid": None,
             "StrategyPriority": None}]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(return_value=_ok({
        "RetargetingLists": [
            {"Id": 90000001, "Type": "RETARGETING", "Name": "Сегмент отказов",
             "IsAvailable": "YES", "Scope": "FOR_TARGETS_AND_ADJUSTMENTS",
             "Rules": [{"Operator": "ANY",
                        "Arguments": [{"ExternalId": 9000000002,
                                       "MembershipLifeSpan": 540}]}],
             "AvailableForTargetsInAdGroupTypes": {
                 "Items": ["TEXT_AD_GROUP", "MAX_ADS_AD_GROUP",
                           "MOBILE_APP_AD_GROUP"]}},
            {"Id": 90000002, "Type": "AUDIENCE",
             "Name": "Интересы и привычки", "IsAvailable": "YES",
             "Scope": "FOR_TARGETS_ONLY",
             "Rules": [{"Operator": "ANY",
                        "Arguments": [{"ExternalId": 900000000002,
                                       "MembershipLifeSpan": 0}]}],
             "AvailableForTargetsInAdGroupTypes": {
                 "Items": ["TEXT_AD_GROUP", "MAX_ADS_AD_GROUP"]}}]}))
    ctx = _ctx(tmp_path, {"9000000002": "Оставленная корзина"})
    out = await ACTIONS["audiences_list"].run(
        ctx, ACTIONS["audiences_list"].params(
            account="agency-login", campaign_ids=[900000002]))
    assert "| Rules |" in out
    assert "| AvailableIn |" in out
    assert "ANY: Оставленная корзина (9000000002) (540 дн.)" in out
    assert "ANY: 900000000002 (0 дн.)" in out
    assert "TEXT_AD_GROUP, MAX_ADS_AD_GROUP, MOBILE_APP_AD_GROUP" in out


# --- п.2: корзина RETARGETING + агрегат ---

def test_criterion_summary_retargeting_bucket():
    rows = [
        {"CriterionType": "KEYWORD", "Impressions": "4", "Clicks": "0",
         "Cost": "0.00", "Conversions": "0"},
        {"CriterionType": "AUTOTARGETING", "Impressions": "510191",
         "Clicks": "667", "Cost": "26788.80", "Conversions": "149"},
        {"CriterionType": "RETARGETING", "Impressions": "19807",
         "Clicks": "65", "Cost": "2207.48", "Conversions": "13"},
    ]
    lines = criterion_summary(rows, "Conversions", Decimal("28996.28"))
    assert lines[3] == "- РЕТАРГЕТИНГ: показы 19 807; клики 65; CTR 0.33%; " \
        "расход 2 207.48 ₽; доля 7.61%; конверсии 13; CPA 169.81 ₽."
    assert len(lines) == 4


def test_audience_aggregate_shares():
    rows = [
        {"CriterionType": "KEYWORD", "Impressions": "4", "Clicks": "0",
         "Cost": "0.00", "Conversions": "0"},
        {"CriterionType": "AUTOTARGETING", "Impressions": "510191",
         "Clicks": "667", "Cost": "26788.80", "Conversions": "149"},
        {"CriterionType": "RETARGETING", "Impressions": "19807",
         "Clicks": "65", "Cost": "2207.48", "Conversions": "13"},
    ]
    total = {"Impressions": Decimal(530002), "Clicks": Decimal(732),
             "Cost": Decimal("28996.28"), "Conversions": Decimal(162)}
    lines = audience_aggregate(rows, "Conversions", total)
    assert lines[0] == "Агрегат по типам критериев (всего строк: 3):"
    assert "Доля РЕТАРГЕТИНГА от итога: показы 3.74%; клики 8.88%; " \
        "расход 7.61%; конверсии 8.02%." in lines


DETAIL = (
    "CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
    "CriterionType\tImpressions\tClicks\tCost\tConversions_9_AUTO\n"
    "900000002\t1\tГруппа\tфраза\t11\tKEYWORD\t4\t0\t0.00\t0\n"
    "900000002\t2\tГруппа\t---autotargeting\t99\tAUTOTARGETING\t"
    "510191\t667\t26788.80\t149\n"
    "900000002\t3\tГруппа\tИнтересы и привычки\t90000013\tRETARGETING\t"
    "19807\t65\t2207.48\t13\n"
)
AGG = "Impressions\tClicks\tCost\tConversions\n530002\t732\t28996.28\t162\n"
RECONC = ("CampaignId\tImpressions\tClicks\tCost\n"
          "900000002\t530002\t732\t28996.28\n")


async def test_stats_audiences_full(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(DETAIL), _tsv(AGG), _tsv(RECONC)])
    respx_mock.post(f"{BASE}/audiencetargets").mock(return_value=_ok({
        "AudienceTargets": [
            {"Id": 90000013, "RetargetingListId": 90000002}]}))
    respx_mock.post(f"{BASE}/retargetinglists").mock(return_value=_ok({
        "RetargetingLists": [
            {"Id": 90000002, "Name": "Интересы и привычки"}]}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_audiences"].run(
        ctx, ACTIONS["stats_audiences"].params(
            account="agency-login", campaign_ids=[900000002], goals=["9"],
            attribution=["AUTO"]))
    assert "Агрегат по типам критериев (всего строк: 3):" in out
    assert "Доля РЕТАРГЕТИНГА от итога:" in out
    assert "Условия ретаргетинга:" in out
    assert "условие 90000013" in out
    assert "Интересы и привычки (90000002)" in out


async def test_stats_audiences_no_retargeting(respx_mock, tmp_path):
    detail = (
        "CampaignId\tAdGroupId\tAdGroupName\tCriterion\tCriterionId\t"
        "CriterionType\tImpressions\tClicks\tCost\n"
        "7\t1\tГруппа\tфраза\t11\tKEYWORD\t10\t1\t100.00\n")
    agg = "Impressions\tClicks\tCost\n10\t1\t100.00\n"
    reconc = "CampaignId\tImpressions\tClicks\tCost\n7\t10\t1\t100.00\n"
    respx_mock.post(RURL).mock(
        side_effect=[_tsv(detail), _tsv(agg), _tsv(reconc)])
    respx_mock.post(f"{BASE}/audiencetargets").mock(
        return_value=_ok({"AudienceTargets": []}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_audiences"].run(
        ctx, ACTIONS["stats_audiences"].params(
            account="agency-login", campaign_ids=[7],
            with_conversions=False))
    assert out.count("Строк RETARGETING нет.") == 2
