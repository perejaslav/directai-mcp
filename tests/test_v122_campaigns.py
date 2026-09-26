"""v1.1.12: расписание, DailyBudget, площадки, статусы, группы через v501."""

import httpx

import directai_mcp.catalog.adgroups as _g  # noqa: F401 (реестр)
import directai_mcp.catalog.campaigns as _c  # noqa: F401 (реестр)
from directai_mcp.catalog.adgroups import _regions, _restricted
from directai_mcp.catalog.campaigns import (
    CampaignsGetParams,
    _fmt_daily,
    _fmt_goals,
    _fmt_holidays,
    _fmt_placements,
    _fmt_schedule,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

V501C = "https://api.direct.yandex.com/json/v501/campaigns"
V501G = "https://api.direct.yandex.com/json/v501/adgroups"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _day(num, hot_hours=()):
    hours = [100 if h in hot_hours else 0 for h in range(24)]
    return ",".join([str(num)] + [str(h) for h in hours])


def _tt():
    return {
        "Schedule": {"Items": [
            _day(d, range(10, 19)) for d in (1, 2, 3, 4, 5)
        ] + [_day(6), _day(7)]},
        "ConsiderWorkingWeekends": "YES",
        "HolidaysSchedule": {"SuspendOnHolidays": "YES"},
    }


def test_schedule_etalon():
    assert _fmt_schedule(_tt()) == "Пн–Пт 10:00–19:00 (100%); Сб–Вс 0%"


def test_schedule_missing_and_malformed():
    assert _fmt_schedule(None) == "—"
    assert _fmt_schedule({}) == "—"
    assert _fmt_schedule({"Schedule": {"Items": ["мусор", "1,2,3"]}}) == "—"
    assert _fmt_schedule({"Schedule": [_day(1, range(24))]}) == \
        "Пн 00:00–24:00 (100%)"


def test_daily_budget():
    assert _fmt_daily({"Amount": 2950000000, "Mode": "STANDARD"}) == \
        "2 950.00 ₽ (STANDARD)"
    assert _fmt_daily(None) == "—"
    assert _fmt_daily({}) == "—"


def test_holidays():
    assert _fmt_holidays(_tt()) == "остановка: YES; рабочие выходные: YES"
    assert "—" in _fmt_holidays(None)


def test_placements():
    search = {"PlacementTypes": {"SearchResults": "YES", "ProductGallery": "YES",
                                 "DynamicPlaces": "YES", "Maps": "NO",
                                 "SearchOrganizationList": "YES"}}
    assert _fmt_placements(search, "UNIFIED_CAMPAIGN") == (
        "SearchResults YES, ProductGallery YES, DynamicPlaces YES, "
        "Maps NO, SearchOrganizationList YES")
    assert _fmt_placements({"PlacementTypes": {"SearchResults": "YES"}},
                           "TEXT_CAMPAIGN") == \
        "SearchResults YES, ProductGallery —, DynamicPlaces —"
    assert _fmt_placements({}, "UNIFIED_CAMPAIGN") == "—"


def test_goals_value_source():
    body = {"PriorityGoals": {"Items": [
        {"GoalId": 1, "Value": 250000000, "IsMetrikaSourceOfValue": "NO"},
        {"GoalId": 2, "Value": 550000000, "IsMetrikaSourceOfValue": "YES"},
        {"GoalId": 3, "Value": 150000000},
    ]}}
    assert _fmt_goals(body, {}) == \
        "1: 250.00 ₽ (фикс); 2: 550.00 ₽ (Метрика); 3: 150.00 ₽ (?)"


def test_regions_excluded_and_restricted():
    assert _regions([1, -213]) == "1; исключены: 213"
    assert _regions([1]) == "1"
    assert _regions([]) == "—"
    assert _regions(None) == "—"
    assert _restricted(None) == "—"
    assert _restricted({"Items": [5, 6]}) == "5, 6"
    assert _restricted([7]) == "7"


async def test_campaigns_get_extended_live_shape(respx_mock, tmp_path):
    item = {
        "Id": 900000003, "Name": "ПОИСК", "Type": "UNIFIED_CAMPAIGN",
        "State": "ON", "Status": "ACCEPTED", "StartDate": "2026-02-24",
        "TimeZone": "Europe/Moscow", "StatusPayment": "ALLOWED",
        "StatusClarification": "Показы начнутся в понедельник в 9:00",
        "DailyBudget": {"Amount": 2950000000, "Mode": "STANDARD"},
        "TimeTargeting": _tt(),
        "UnifiedCampaign": {
            "BiddingStrategy": {
                "Search": {
                    "BiddingStrategyType": "HIGHEST_POSITION",
                    "PlacementTypes": {"SearchResults": "YES", "Maps": "NO"},
                    "HighestPosition": {"WeeklySpendLimit": 15300000000},
                },
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            },
            "PriorityGoals": {"Items": [
                {"GoalId": 9, "Value": 250000000,
                 "IsMetrikaSourceOfValue": "NO"}]},
            "AttributionModel": "AUTO",
            "TrackingParams": "utm_source=yandex",
            "CounterIds": {"Items": [90000004]},
        },
    }
    respx_mock.post(V501C).mock(
        return_value=httpx.Response(200, json={"result": {"Campaigns": [item]}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaigns_get"].run(
        ctx, CampaignsGetParams(account="m", campaign_ids=[900000003], full=True))
    assert "2 950.00 ₽ (STANDARD)" in out
    assert "Показы начнутся в понедельник в 9:00" in out
    assert "Пн–Пт 10:00–19:00 (100%); Сб–Вс 0%" in out
    assert "Maps NO" in out
    assert "остановка: YES; рабочие выходные: YES" in out
    assert "9: 250.00 ₽ (фикс)" in out


async def test_adgroups_list_v501_type_and_regions(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(V501G).mock(return_value=httpx.Response(200, json={
        "result": {"AdGroups": [
            {"Id": 1, "CampaignId": 7, "Name": "Г", "Status": "ACCEPTED",
             "ServingStatus": "ELIGIBLE", "RegionIds": [1, -213],
             "RestrictedRegionIds": None, "Type": "UNIFIED_AD_GROUP"},
        ]}}))
    respx_mock.post("https://api.direct.yandex.com/json/v5/keywords").mock(
        return_value=httpx.Response(200, json={"result": {"Keywords": []}}))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["adgroups_list"].run(
        ctx, ACTIONS["adgroups_list"].params(account="m", campaign_ids=[7]))
    assert route.call_count == 1  # только v501: v5 не мокали
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "RestrictedRegionIds" in sent["FieldNames"]
    assert "UNIFIED_AD_GROUP" in out
    assert "TEXT_AD_GROUP" not in out
    assert "исключены: 213" in out
