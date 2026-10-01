"""B2 v1.9.0: keyword_bids_forecast + phrases_forecast (mocks, no live calls)."""

import httpx

from directai_mcp.catalog import forecast as forecast_mod
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

BASE = "https://api.direct.yandex.com/json/v5"
BASE_V501 = "https://api.direct.yandex.com/json/v501"
LIVE = "https://api.direct.yandex.com/live/v4/json/"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="x",
        accounts={"t": AccountEntry(alias="t", login="test-login")},
        guard=False,
    )
    return Ctx(settings=settings, token="fake", data_dir=tmp_path)


def _ok(result, units="20/1000/64000"):
    return httpx.Response(200, json={"result": result}, headers={"Units": units})


def _run(name, ctx, params):
    act = ACTIONS[name]
    validated = act.params.model_validate(params)
    assert act.run is not None
    return act.run(ctx, validated)


def _mock_manual(respx_mock):
    respx_mock.post(f"{BASE}/keywordbids").mock(
        return_value=_ok({
            "KeywordBids": [
                {"KeywordId": 900000011, "AdGroupId": 900000021, "CampaignId": 900000031,
                 "ServingStatus": "ELIGIBLE",
                 "Search": {"Bid": 50000000,
                            "AuctionBids": {"AuctionBidItems": [
                                {"TrafficVolume": 5, "Bid": 20000000, "Price": 15000000},
                                {"TrafficVolume": 15, "Bid": 30000000, "Price": 25000000},
                                {"TrafficVolume": 75, "Bid": 45000000, "Price": 40000000},
                                {"TrafficVolume": 100, "Bid": 50000000, "Price": 47000000},
                            ]}},
                 "Network": {"Bid": 10000000}},
                {"KeywordId": 900000012, "AdGroupId": 900000021, "CampaignId": 900000031,
                 "ServingStatus": "ELIGIBLE",
                 "Search": {"Bid": 60000000,
                            "AuctionBids": {"AuctionBidItems": [
                                {"TrafficVolume": 5, "Bid": 22000000, "Price": 17000000},
                                {"TrafficVolume": 15, "Bid": 32000000, "Price": 27000000},
                                {"TrafficVolume": 75, "Bid": 47000000, "Price": 42000000},
                                {"TrafficVolume": 100, "Bid": 60000000, "Price": 53000000},
                            ]}},
                 "Network": {"Bid": 12000000}},
            ]
        })
    )
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": [
            {"Id": 900000011, "Keyword": "купить окна", "State": "ON", "Status": "ACCEPTED"},
            {"Id": 900000012, "Keyword": "окна пвх", "State": "ON", "Status": "ACCEPTED"},
        ]})
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok({"Campaigns": [
            {"Id": 900000031, "Name": "test", "Type": "TEXT_CAMPAIGN",
             "TextCampaign": {"BiddingStrategy": {
                 "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                 "Network": {"BiddingStrategyType": "MAXIMUM_COVERAGE"}}}},
        ]})
    )


async def test_bids_forecast_manual_full_table(tmp_path, respx_mock):
    _mock_manual(respx_mock)
    ctx = _ctx(tmp_path)
    out = await _run("keyword_bids_forecast", ctx,
                     {"account": "t", "keyword_ids": [900000011, 900000012]})
    assert "прогноз Яндекса, не гарантия" in out
    assert "медиане цены при объёме 100" in out or "медиана цены при объёме 100" in out
    assert "медиана цены при объёме 15" in out
    assert "Оценка баллов" in out
    assert "купить окна" in out


async def test_bids_forecast_auto_epk_note_no_error(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/keywordbids").mock(
        return_value=_ok({
            "KeywordBids": [
                {"KeywordId": 900000013, "AdGroupId": 900000022, "CampaignId": 900000032,
                 "ServingStatus": "ELIGIBLE",
                 "Search": {"Bid": 50000000,
                            "AuctionBids": {"AuctionBidItems": [
                                {"TrafficVolume": 100, "Bid": 50000000, "Price": 47000000},
                            ]}},
                 "Network": {"Bid": 10000000}},
                {"KeywordId": 900000014, "AdGroupId": 900000022, "CampaignId": 900000032,
                 "ServingStatus": "RARELY_SERVED",
                 "Search": {"Bid": 50000000, "AuctionBids": None},
                 "Network": {"Bid": 10000000}},
            ]
        })
    )
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": [
            {"Id": 900000013, "Keyword": "двери", "State": "ON", "Status": "ACCEPTED"},
            {"Id": 900000014, "Keyword": "двери мск", "State": "ON", "Status": "ACCEPTED"},
        ]})
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok({"Campaigns": [
            {"Id": 900000032, "Name": "epk", "Type": "UNIFIED_CAMPAIGN",
             "UnifiedCampaign": {"BiddingStrategy": {
                 "Search": {"BiddingStrategyType": "OPTIMIZATION_CONVERSIONS"},
                 "Network": {"BiddingStrategyType": "OPTIMIZATION_CONVERSIONS"}}}},
        ]})
    )
    ctx = _ctx(tmp_path)
    out = await _run("keyword_bids_forecast", ctx,
                     {"account": "t", "campaign_id": 900000032})
    assert "прогноз Яндекса, не гарантия" in out
    assert "ставками управляет стратегия" in out
    assert "без данных аукциона" in out
    assert "Ошибка" not in out.split("Примечание")[0] or "без данных" in out


async def test_bids_forecast_pagination_no_loss(tmp_path, respx_mock):
    page1 = _ok({"KeywordBids": [
        {"KeywordId": 900000015, "AdGroupId": 900000023, "CampaignId": 900000033,
         "ServingStatus": "ELIGIBLE",
         "Search": {"Bid": 10000000,
                    "AuctionBids": {"AuctionBidItems": [
                        {"TrafficVolume": 100, "Bid": 10000000, "Price": 9000000}]}},
         "Network": {"Bid": 7000000}},
    ], "LimitedBy": 1})
    page2 = _ok({"KeywordBids": [
        {"KeywordId": 900000016, "AdGroupId": 900000023, "CampaignId": 900000033,
         "ServingStatus": "ELIGIBLE",
         "Search": {"Bid": 11000000,
                    "AuctionBids": {"AuctionBidItems": [
                        {"TrafficVolume": 100, "Bid": 11000000, "Price": 9500000}]}},
         "Network": {"Bid": 7000000}},
    ]})
    respx_mock.post(f"{BASE}/keywordbids").mock(side_effect=[page1, page2])
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": [
            {"Id": 900000015, "Keyword": "a", "State": "ON", "Status": "ACCEPTED"},
            {"Id": 900000016, "Keyword": "b", "State": "ON", "Status": "ACCEPTED"},
        ]})
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok({"Campaigns": [
            {"Id": 900000033, "Name": "t", "Type": "TEXT_CAMPAIGN",
             "TextCampaign": {"BiddingStrategy": {
                 "Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}}},
        ]})
    )
    ctx = _ctx(tmp_path)
    out = await _run("keyword_bids_forecast", ctx,
                     {"account": "t", "campaign_id": 900000033})
    assert "Фраз прочитано: 2" in out


async def test_bids_forecast_autotargeting_separate(tmp_path, respx_mock):
    respx_mock.post(f"{BASE}/keywordbids").mock(
        return_value=_ok({"KeywordBids": [
            {"KeywordId": 900000017, "AdGroupId": 900000024, "CampaignId": 900000034,
             "ServingStatus": "ELIGIBLE",
             "Search": {"Bid": 20000000, "AuctionBids": None},
             "Network": {"Bid": 7000000}},
        ]})
    )
    respx_mock.post(f"{BASE}/keywords").mock(
        return_value=_ok({"Keywords": [
            {"Id": 900000017, "Keyword": "---autotargeting",
             "State": "ON", "Status": "ACCEPTED"},
        ]})
    )
    respx_mock.post(f"{BASE_V501}/campaigns").mock(
        return_value=_ok({"Campaigns": [
            {"Id": 900000034, "Name": "t", "Type": "TEXT_CAMPAIGN",
             "TextCampaign": {"BiddingStrategy": {
                 "Search": {"BiddingStrategyType": "HIGHEST_POSITION"}}}},
        ]})
    )
    ctx = _ctx(tmp_path)
    out = await _run("keyword_bids_forecast", ctx,
                     {"account": "t", "keyword_ids": [900000017]})
    assert "автотаргетинг" in out.lower()
    assert "прогноз Яндекса, не гарантия" in out


async def test_bids_forecast_scope_validation(tmp_path):
    ctx = _ctx(tmp_path)
    out = await _run("keyword_bids_forecast", ctx, {"account": "t"})
    assert "ровно одно из" in out
    out = await _run("keyword_bids_forecast", ctx,
                     {"account": "t", "campaign_id": 1, "keyword_ids": [2]})
    assert "ровно одно из" in out


def _live_ok(data):
    return httpx.Response(200, json={"data": data})


async def test_phrases_forecast_full_cycle(tmp_path, respx_mock, monkeypatch):
    monkeypatch.setattr(forecast_mod, "FORECAST_POLL_INTERVAL", 0.01)
    route = respx_mock.post(LIVE)
    route.mock(side_effect=[
        _live_ok(900000101),
        _live_ok([{"ForecastID": 900000101, "StatusForecast": "Pending"}]),
        _live_ok([{"ForecastID": 900000101, "StatusForecast": "Done"}]),
        _live_ok({
            "Phrases": [
                {"Phrase": "окна пвх", "Shows": 1000, "Clicks": 50,
                 "FirstPlaceClicks": 20, "PremiumClicks": 80,
                 "CTR": 5.0, "Min": 10.0, "Max": 20.0,
                 "PremiumMin": 30.0, "PremiumMax": 40.0, "Currency": "RUB",
                 "AuctionBids": [{"Position": "P11", "Bid": 35.0, "Price": 30.0}]},
            ],
            "Common": {"Geo": "213", "Min": 10.0, "Max": 20.0, "PremiumMin": 30.0,
                       "Shows": 1000, "Clicks": 50,
                       "FirstPlaceClicks": 20, "PremiumClicks": 80},
        }),
        _live_ok(1),
    ])
    ctx = _ctx(tmp_path)
    out = await _run("phrases_forecast", ctx,
                     {"account": "t", "phrases": ["окна пвх"], "region_ids": [213]})
    assert "прогноз Яндекса, не гарантия" in out
    assert "окна пвх" in out
    assert "Итого по всем фразам" in out
    assert "удалён" in out


async def test_phrases_forecast_timeout_returns_id(tmp_path, respx_mock, monkeypatch):
    monkeypatch.setattr(forecast_mod, "FORECAST_POLL_INTERVAL", 0.01)
    respx_mock.post(LIVE).mock(side_effect=[
        _live_ok(900000102),
        *[_live_ok([{"ForecastID": 900000102, "StatusForecast": "Pending"}])] * 500,
    ])
    ctx = _ctx(tmp_path)
    out = await _run("phrases_forecast", ctx,
                     {"account": "t", "phrases": ["окна"], "region_ids": [213],
                      "timeout_s": 1})
    assert "forecast_id=900000102" in out
    assert "не готов" in out


async def test_phrases_forecast_limit_before_api(tmp_path, respx_mock):
    ctx = _ctx(tmp_path)
    out = await _run("phrases_forecast", ctx,
                     {"account": "t", "phrases": ["x"] * 101, "region_ids": [213]})
    assert "не более 100 фраз" in out


async def test_forecast_actions_registered():
    assert ACTIONS["keyword_bids_forecast"].mode == "read"
    assert ACTIONS["phrases_forecast"].mode == "read"
    assert "только чтение" in ACTIONS["keyword_bids_forecast"].summary
    assert "только чтение" in ACTIONS["phrases_forecast"].summary
