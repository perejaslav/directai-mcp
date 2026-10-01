"""v1.3.0: dump-действия шага 0 — стратегии, фиды, таргетинг, бизнес, турбо."""

import directai_mcp.catalog.ads as ads_mod
import directai_mcp.catalog.dump as dump_mod
from directai_mcp.catalog.registry import ACTIONS, Ctx, search
from directai_mcp.config import AccountEntry, Settings

NAMES = (
    "strategies_get",
    "feeds_get",
    "dynamic_targets_get",
    "dynamic_feed_targets_get",
    "smart_targets_get",
    "businesses_get",
    "turbopages_get",
)


def _ctx(tmp_path) -> Ctx:
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="main-token", data_dir=tmp_path)


def _entry() -> AccountEntry:
    return AccountEntry(alias="m", login="agency-login")


def test_dump_actions_registered_read_only():
    for name in NAMES:
        act = ACTIONS[name]
        assert act.mode == "read"
        assert act.run is not None
        assert act.prepare is None and act.apply is None and act.verify is None
    assert "vcards_get" not in ACTIONS  # визитки удалены Яндексом (3500)


def test_dump_params_schema():
    assert not dump_mod.StrategiesGetParams.model_fields[
        "strategy_ids"].is_required()
    assert not dump_mod.DynamicTargetsGetParams.model_fields[
        "states"].is_required()
    assert not dump_mod.BusinessesGetParams.model_fields[
        "business_ids"].is_required()
    assert not ads_mod.AdsListParams.model_fields["states"].is_required()
    assert "архивные" in ACTIONS["ads_list"].keywords


def test_dump_search_synonyms():
    found = {a.name for a in search("пакетная стратегия", mode="read")}
    assert "strategies_get" in found
    found = {a.name for a in search("фид каталог товаров", mode="read")}
    assert "feeds_get" in found
    found = {a.name for a in search("условия нацеливания динамических",
                                    mode="read")}
    assert "dynamic_targets_get" in found
    found = {a.name for a in search("смарт-баннеры фильтры", mode="read")}
    assert "smart_targets_get" in found
    found = {a.name for a in search("профиль организации", mode="read")}
    assert "businesses_get" in found
    found = {a.name for a in search("турбо-страница", mode="read")}
    assert "turbopages_get" in found


def test_dump_scope_errors_without_network(monkeypatch, tmp_path):
    async def no_network(*args, **kwargs):
        raise AssertionError("network must not be touched")

    monkeypatch.setattr(dump_mod, "map_accounts", no_network)
    import asyncio

    out = asyncio.run(ACTIONS["businesses_get"].run(
        _ctx(tmp_path), dump_mod.BusinessesGetParams()))
    assert "business_ids" in out
    out = asyncio.run(ACTIONS["dynamic_targets_get"].run(
        _ctx(tmp_path), dump_mod.DynamicTargetsGetParams()))
    assert "campaign_ids" in out


def _fake_map(items):
    async def fake(ctx, account_value, fn):
        return [(_entry(), items)]

    return fake


def test_strategies_renders_offline(monkeypatch, tmp_path):
    items = [{
        "Id": 900000001,
        "Name": "Пакет",
        "Type": "AVERAGE_CPA",
        "StatusArchived": "NO",
        "AttributionModel": "AUTO",
        "CounterIds": {"Items": [90000004]},
        "PriorityGoals": {"Items": [{"GoalId": 900000007,
                                     "Value": 600000001}]},
        "AverageCpa": {"AverageCpa": 500000001, "GoalId": 900000007,
                       "WeeklySpendLimit": 14000000000},
    }]
    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map(items))
    import asyncio

    out = asyncio.run(ACTIONS["strategies_get"].run(
        _ctx(tmp_path), dump_mod.StrategiesGetParams()))
    assert "Пакет" in out and "AVERAGE_CPA" in out
    assert "600" in out and "500" in out  # micros -> рубли


def test_dynamic_targets_renders_offline(monkeypatch, tmp_path):
    items = [{
        "Id": 900000002,
        "CampaignId": 900000031,
        "AdGroupId": 9000000010,
        "Name": "Все страницы",
        "State": "ON",
        "Bid": 120000000,
        "ContextBid": 300000,
        "StrategyPriority": "NORMAL",
        "ConditionType": "PAGES_ALL",
        "Conditions": [{"Operand": "URL", "Operator": "CONTAINS_ANY",
                        "Arguments": ["example.com"]}],
    }]
    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map(items))
    import asyncio

    out = asyncio.run(ACTIONS["dynamic_targets_get"].run(
        _ctx(tmp_path),
        dump_mod.DynamicTargetsGetParams(campaign_ids=[900000031])))
    assert "URL" in out and "example.com" in out and "120" in out


def test_feed_smart_shapes_renders_offline(monkeypatch, tmp_path):
    import asyncio

    feed_items = [{
        "Id": 900000003,
        "Name": "Каталог",
        "State": "ON",
        "Bid": 100000001,
        "ContextBid": None,
        "ConditionType": "ITEMS_SUBSET",
        "Conditions": {"Items": [{"Operand": "PRICE",
                                  "Operator": "EQUALS_ANY",
                                  "Arguments": ["100"]}]},
        "AvailableItemsOnly": "YES",
        "CampaignId": 900000031,
        "AdGroupId": 9000000010,
    }]
    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map(feed_items))
    out = asyncio.run(ACTIONS["dynamic_feed_targets_get"].run(
        _ctx(tmp_path),
        dump_mod.DynamicFeedTargetsGetParams(campaign_ids=[900000031])))
    assert "PRICE" in out and "подмножество товаров" in out

    smart_items = [{
        "Id": 900000004,
        "Name": "Фильтр",
        "State": "ON",
        "AverageCpc": 50000000,
        "AverageCpa": None,
        "StrategyPriority": "HIGH",
        "Audience": "ALL_SEGMENTS",
        "ConditionType": "ITEMS_ALL",
        "Conditions": None,
        "AvailableItemsOnly": "NO",
        "CampaignId": 900000031,
        "AdGroupId": 9000000010,
    }]
    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map(smart_items))
    out = asyncio.run(ACTIONS["smart_targets_get"].run(
        _ctx(tmp_path),
        dump_mod.SmartTargetsGetParams(campaign_ids=[900000031])))
    assert "ALL_SEGMENTS" in out and "все товары" in out


def test_feeds_businesses_turbo_renders_offline(monkeypatch, tmp_path):
    import asyncio

    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map([{
        "Id": 900000005,
        "Name": "Fid",
        "BusinessType": "RETAIL",
        "SourceType": "URL",
        "Status": "DONE",
        "NumberOfItems": 42,
        "UpdatedAt": "2026-09-01",
        "UrlFeed": {"Url": "https://example.com/feed.xml"},
        "CampaignIds": {"Items": [900000031]},
        "FilterSchema": "retail",
        "TitleAndTextSources": {"Items": ["name"]},
    }]))
    out = asyncio.run(ACTIONS["feeds_get"].run(
        _ctx(tmp_path), dump_mod.FeedsGetParams()))
    assert "Fid" in out and "example.com" in out and "42" in out

    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map([{
        "Id": 900000006,
        "Name": "Org",
        "Address": "Москва",
        "Phone": "+7",
        "IsPublished": "YES",
        "Rubric": "Стройка",
        "Urls": {"Items": ["https://example.com"]},
        "ProfileUrl": "https://example.com/profile",
    }]))
    out = asyncio.run(ACTIONS["businesses_get"].run(
        _ctx(tmp_path), dump_mod.BusinessesGetParams(
            business_ids=[900000006])))
    assert "Org" in out and "Москва" in out

    monkeypatch.setattr(dump_mod, "map_accounts", _fake_map([{
        "Id": 900000007,
        "Name": "Turbo",
        "Href": "https://example.com/t",
        "PreviewHref": "https://example.com/p",
        "TurboSiteHref": "example.turbo.site",
    }]))
    out = asyncio.run(ACTIONS["turbopages_get"].run(
        _ctx(tmp_path), dump_mod.TurboPagesGetParams()))
    assert "Turbo" in out and "turbo.site" in out


def test_get_all_page_limit_override(monkeypatch):
    import asyncio

    from directai_mcp.api.direct import DirectClient

    seen: list[dict] = []

    async def fake_call(self, service, method, params, login, version="v5"):
        seen.append(params["Page"])
        return {"Businesses": []}

    monkeypatch.setattr(DirectClient, "call", fake_call)
    client = DirectClient(token="t")
    out = asyncio.run(client.get_all(
        "businesses", {"SelectionCriteria": {"Ids": [1]}},
        "agency-login", "Businesses", page_limit=1000))
    assert out == []
    assert seen == [{"Limit": 1000, "Offset": 0}]


def test_ads_states_filter_passed(monkeypatch, tmp_path):
    import asyncio

    seen: list[dict] = []

    async def fake(ctx, account_value, fn):
        class Client:
            async def get_all(self, service, params, login, key):
                seen.append(params["SelectionCriteria"])
                return []

            async def aclose(self):
                pass

        entry = _entry()
        await fn(entry, Client())
        return [(entry, ([], {}, {}, {}))]

    monkeypatch.setattr(ads_mod, "map_accounts", fake)
    asyncio.run(ACTIONS["ads_list"].run(
        _ctx(tmp_path),
        ads_mod.AdsListParams(campaign_ids=[900000031],
                              states=["ARCHIVED"])))
    assert seen and seen[0].get("States") == ["ARCHIVED"]
    seen.clear()
    asyncio.run(ACTIONS["ads_list"].run(
        _ctx(tmp_path), ads_mod.AdsListParams(campaign_ids=[900000031])))
    assert seen and "States" not in seen[0]
