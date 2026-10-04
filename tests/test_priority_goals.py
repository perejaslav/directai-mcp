"""v1.16.0: ключевые цели (PriorityGoals) в campaigns_update.

Сериализация ₽ -> микро-единицы, валидация (пакетная стратегия, дубли
goal_id, пустой набор), план с before -> after, read-back и полный цикл
plan_write -> apply_write -> read-back. Только моки: живые кампании не
трогаются.
"""

import asyncio

import httpx
import pytest

import directai_mcp.catalog.campaigns as _cm  # noqa: F401 (реестр)
from directai_mcp.catalog.campaigns import (
    CampaignsUpdateParams,
    _goal_item,
    _prepare_campaigns_update,
    _rub_to_micros,
    _verify_campaigns_updated,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety.guard import CONFIRM_SUFFIX, DANGER_NOTICE, GOALS_BLOCK
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

V5 = "https://api.direct.yandex.com/json/v5"
V501 = "https://api.direct.yandex.com/json/v501"
TEST_ID = 700007
TEST_NAME = "[TEST DirectAI] Цели"
COMBAT_ID = 900000009


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """read-back при расхождении ждёт 10 с (репликация API) — в тестах нет."""

    async def _fast(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _fast)


def _ctx(tmp_path, mode="confirm"):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        guard_mode=mode,
        goal_names={"201": "Заявка", "202": "Покупка"},
        goal_counters={"201": 3},
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _entry():
    return AccountEntry(alias="m", login="agency-login")


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _item(**kw):
    return _ok({"Campaigns": [kw]})


def _unified(cid=TEST_ID, goals=None, **extra):
    body = {"Id": cid, "Name": TEST_NAME, "Type": "UNIFIED_CAMPAIGN"}
    if goals is not None:
        body["UnifiedCampaign"] = {"PriorityGoals": {"Items": goals}}
    return _item(**body, **extra)


def _params(**kw):
    return ACTIONS["campaigns_update"].params(account="m", campaign_ids=[TEST_ID], **kw)


def _pid(out: str) -> str:
    assert out.startswith("План "), out
    return out.split()[1].rstrip(":")


# -- сериализация ₽ -> Value (микро-единицы) --------------------------------


def test_rub_to_micros_exact():
    assert _rub_to_micros(500) == 500_000_000
    assert _rub_to_micros(1234.56) == 1_234_560_000
    assert _rub_to_micros(0.01) == 10_000
    # 0.1 + 0.2 не должен давать 300000.00000000006 -> обрезанную сумму.
    assert _rub_to_micros(0.1) + _rub_to_micros(0.2) == 300_000


def test_rub_to_micros_rejects_zero_micros():
    with pytest.raises(ValueError, match="микро-единиц"):
        _rub_to_micros(0.0000004)


def test_goal_item_shape():
    from directai_mcp.catalog.campaigns import PriorityGoal

    item = _goal_item(PriorityGoal(goal_id=201, value_rub=500.5))
    assert item == {
        "GoalId": 201,
        "Value": 500_500_000,
        "Operation": "SET",
        "IsMetrikaSourceOfValue": "NO",
    }
    metrika = _goal_item({"goal_id": 12, "value_rub": 10.0,
                          "is_metrika_source_of_value": True})
    assert metrika["IsMetrikaSourceOfValue"] == "YES"
    assert metrika["GoalId"] == 12


def test_params_reject_non_positive_value():
    with pytest.raises(ValueError):
        CampaignsUpdateParams(account="m", campaign_ids=[TEST_ID],
                              priority_goals=[{"goal_id": 201, "value_rub": 0}])
    with pytest.raises(ValueError):
        CampaignsUpdateParams(account="m", campaign_ids=[TEST_ID],
                              priority_goals=[{"goal_id": 0, "value_rub": 10}])


# -- план: тело запроса и before -> after ------------------------------------


async def test_prepare_builds_items_and_preview(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified(
        goals=[{"GoalId": 201, "Value": 300_000_000,
                "IsMetrikaSourceOfValue": "NO"}]))
    prep = await _prepare_campaigns_update(_ctx(tmp_path), _entry(), _params(
        priority_goals=[{"goal_id": 201, "value_rub": 500},
                        {"goal_id": 202, "value_rub": 250.25,
                         "is_metrika_source_of_value": True}]))
    service, method, body, version = prep["requests"][0]
    assert (service, method, version) == ("campaigns", "update", "v501")
    item = body["Campaigns"][0]
    assert item["UnifiedCampaign"]["PriorityGoals"]["Items"] == [
        {"GoalId": 201, "Value": 500_000_000, "Operation": "SET",
         "IsMetrikaSourceOfValue": "NO"},
        {"GoalId": 202, "Value": 250_250_000, "Operation": "SET",
         "IsMetrikaSourceOfValue": "YES"},
    ]
    # before -> after в предпросмотре (ценности в ₽, имя цели из goals.toml).
    assert "Заявка (201, счётчик 3): 300.00 ₽ (фикс)" in prep["preview"]
    assert "Заявка (201, счётчик 3): 500.00 ₽ (фикс)" in prep["preview"]
    assert "Покупка (202): 250.25 ₽ (Метрика)" in prep["preview"]
    assert "Смена стратегии/целей сбрасывает обучение кампании." in prep["warnings"]


async def test_prepare_text_campaign_uses_v5(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_item(
        Id=TEST_ID, Name=TEST_NAME, Type="TEXT_CAMPAIGN"))
    prep = await _prepare_campaigns_update(
        _ctx(tmp_path), _entry(), _params(
            priority_goals=[{"goal_id": 201, "value_rub": 100}]))
    _service, _method, body, version = prep["requests"][0]
    assert version == "v5"
    assert body["Campaigns"][0]["TextCampaign"]["PriorityGoals"]["Items"][0][
        "Value"] == 100_000_000


async def test_prepare_with_strategy_keeps_both_blocks(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified())
    prep = await _prepare_campaigns_update(
        _ctx(tmp_path), _entry(), _params(
            strategy={"Search": {"BiddingStrategyType": "AVERAGE_CPA"},
                      "Network": {"BiddingStrategyType": "SERVING_OFF"}},
            priority_goals=[{"goal_id": 201, "value_rub": 100},
                            {"goal_id": 202, "value_rub": 100}]))
    block = prep["requests"][0][2]["Campaigns"][0]["UnifiedCampaign"]
    assert "BiddingStrategy" in block and "PriorityGoals" in block
    # Одно предупреждение, а не два.
    assert prep["warnings"].count(
        "Смена стратегии/целей сбрасывает обучение кампании.") == 1


async def test_reset_sends_null(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified(
        goals=[{"GoalId": 201, "Value": 300_000_000}]))
    prep = await _prepare_campaigns_update(
        _ctx(tmp_path), _entry(), _params(priority_goals_reset=True))
    item = prep["requests"][0][2]["Campaigns"][0]
    assert item["UnifiedCampaign"]["PriorityGoals"] is None
    assert "набор сброшен (PriorityGoals=null) → вовлечённые сессии" in prep["preview"]


# -- валидация до API ---------------------------------------------------------


async def test_duplicate_goal_id_rejected(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified())
    with pytest.raises(ValueError, match="повторяется goal_id 201"):
        await _prepare_campaigns_update(
            _ctx(tmp_path), _entry(), _params(
                priority_goals=[{"goal_id": 201, "value_rub": 100},
                                {"goal_id": 201, "value_rub": 200}]))


async def test_empty_list_needs_explicit_reset(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified())
    with pytest.raises(ValueError, match="только явный сброс"):
        await _prepare_campaigns_update(
            _ctx(tmp_path), _entry(), _params(priority_goals=[]))


async def test_goals_and_reset_together_rejected():
    with pytest.raises(ValueError, match="вместе нельзя"):
        CampaignsUpdateParams(
            account="m", campaign_ids=[TEST_ID], priority_goals_reset=True,
            priority_goals=[{"goal_id": 201, "value_rub": 100}])


async def test_package_strategy_param_conflict():
    """Ключи пакетной стратегии модель отбросила бы (extra=ignore) — отказ."""
    with pytest.raises(ValueError, match="пакетной стратегией"):
        CampaignsUpdateParams.model_validate({
            "account": "m", "campaign_ids": [TEST_ID],
            "priority_goals": [{"goal_id": 201, "value_rub": 100}],
            "package_strategy_id": 55,
        })


async def test_bound_campaign_refused(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_item(
        Id=TEST_ID, Name=TEST_NAME, Type="UNIFIED_CAMPAIGN",
        UnifiedCampaign={"PackageBiddingStrategy": {"StrategyId": 55}}))
    with pytest.raises(ValueError, match="пакетной стратегии"):
        await _prepare_campaigns_update(
            _ctx(tmp_path), _entry(), _params(
                priority_goals=[{"goal_id": 201, "value_rub": 100}]))


# -- read-back ----------------------------------------------------------------


def _plan(goals=None, reset=False, version="v501"):
    from types import SimpleNamespace

    params = {"campaign_ids": [TEST_ID]}
    if goals is not None:
        params["priority_goals"] = goals
    if reset:
        params["priority_goals_reset"] = True
    return SimpleNamespace(
        params=params,
        requests=[("campaigns", "update", {"Campaigns": [{"Id": TEST_ID}]},
                   version)],
    )


async def test_read_back_matches(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified(goals=[
        {"GoalId": 201, "Value": 500_000_000, "IsMetrikaSourceOfValue": "NO"},
        {"GoalId": 202, "Value": 250_250_000, "IsMetrikaSourceOfValue": "YES"},
    ]))
    result = await _verify_campaigns_updated(_ctx(tmp_path), _entry(), _plan(
        goals=[{"goal_id": 201, "value_rub": 500},
               {"goal_id": 202, "value_rub": 250.25,
                "is_metrika_source_of_value": True}]))
    assert result["ok"] is True
    assert "Ключевые цели после записи" in result["note"]
    assert "Заявка (201, счётчик 3): 500.00 ₽" in result["note"]


async def test_read_back_mismatch(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified(goals=[
        {"GoalId": 201, "Value": 300_000_000, "IsMetrikaSourceOfValue": "NO"},
    ]))
    result = await _verify_campaigns_updated(_ctx(tmp_path), _entry(), _plan(
        goals=[{"goal_id": 201, "value_rub": 500}]))
    assert result["ok"] is False
    assert f"{TEST_ID}.priority_goals" in result["note"]
    assert "read-back НЕ подтвердил" in result["note"]


async def test_read_back_reset(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_item(
        Id=TEST_ID, Name=TEST_NAME, Type="TEXT_CAMPAIGN"))
    result = await _verify_campaigns_updated(
        _ctx(tmp_path), _entry(), _plan(reset=True, version="v5"))
    assert result["ok"] is True
    assert "набор отсутствует" in result["note"]


# -- guard: блок в block, опасная операция в confirm --------------------------


async def test_block_mode_refuses_before_api(tmp_path):
    out = await do_plan_write(_ctx(tmp_path, mode="block"), "campaigns_update", {
        "account": "m", "campaign_ids": [TEST_ID],
        "priority_goals": [{"goal_id": 201, "value_rub": 500}]})
    assert out.startswith(f"Заблокировано защитой: {GOALS_BLOCK}"), out


async def test_confirm_mode_is_danger(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_item(
        Id=TEST_ID, Name=TEST_NAME, Type="UNIFIED_CAMPAIGN"))
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified())
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
        "account": "m", "campaign_ids": [TEST_ID],
        "priority_goals": [{"goal_id": 201, "value_rub": 500}]})
    pid = _pid(out)
    assert DANGER_NOTICE in out
    assert f"изменение ключевых целей (ценностей конверсий) кампании — {CONFIRM_SUFFIX}" in out
    refused = await do_apply_write(_ctx(tmp_path), pid)
    assert "ОПАСНАЯ ОПЕРАЦИЯ" in refused
    refused = await do_apply_write(
        _ctx(tmp_path), pid, owner_confirmed=True)
    assert "acknowledge_warnings=true" in refused


async def test_combat_campaign_still_blocks_writes(respx_mock, tmp_path):
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_item(
        Id=COMBAT_ID, Name="ПОИСК - Эталон", Type="UNIFIED_CAMPAIGN"))
    out = await do_plan_write(_ctx(tmp_path, mode="block"), "campaigns_update", {
        "account": "m", "campaign_ids": [COMBAT_ID],
        "priority_goals": [{"goal_id": 201, "value_rub": 500}]})
    assert "Заблокировано защитой" in out


# -- полный цикл: plan_write -> apply_write -> read-back ----------------------


async def test_full_cycle_plan_apply_readback(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.post(f"{V5}/campaigns").mock(return_value=_item(
        Id=TEST_ID, Name=TEST_NAME, Type="UNIFIED_CAMPAIGN"))
    respx_mock.post(f"{V501}/campaigns").mock(side_effect=[
        _unified(goals=[{"GoalId": 201, "Value": 300_000_000,
                         "IsMetrikaSourceOfValue": "NO"}]),
        _ok({"UpdateResults": [{"Id": TEST_ID}]}),
        _unified(goals=[{"GoalId": 201, "Value": 750_000_000,
                         "IsMetrikaSourceOfValue": "NO"}]),
    ])
    out = await do_plan_write(ctx, "campaigns_update", {
        "account": "m", "campaign_ids": [TEST_ID],
        "priority_goals": [{"goal_id": 201, "value_rub": 750}]})
    pid = _pid(out)
    assert "Заявка (201, счётчик 3): 300.00 ₽" in out
    assert "Заявка (201, счётчик 3): 750.00 ₽" in out
    plan = PLANS.peek(pid)
    assert plan.danger == [
        f"изменение ключевых целей (ценностей конверсий) кампании — {CONFIRM_SUFFIX}"
    ]
    assert plan.warnings == ["Смена стратегии/целей сбрасывает обучение кампании."]
    applied = await do_apply_write(
        ctx, pid, acknowledge_warnings=True, owner_confirmed=True)
    assert "статус applied" in applied, applied
    assert "Ключевые цели после записи" in applied
    assert "Заявка (201, счётчик 3): 750.00 ₽" in applied
    assert "Запись журнала #" in applied
    again = await do_apply_write(
        ctx, pid, acknowledge_warnings=True, owner_confirmed=True)
    assert "уже применён" in again


# -- campaigns_get: чтение ключевых целей ------------------------------------


async def test_campaigns_get_reads_priority_goals(respx_mock, tmp_path):
    respx_mock.post(f"{V501}/campaigns").mock(return_value=_unified(goals=[
        {"GoalId": 201, "Value": 500_000_000, "IsMetrikaSourceOfValue": "NO"},
    ]))
    out = await ACTIONS["campaigns_get"].run(
        _ctx(tmp_path), ACTIONS["campaigns_get"].params(
            account="m", campaign_ids=[TEST_ID]))
    assert "Заявка (201, счётчик 3): 500.00 ₽ (фикс)" in out
    import json

    sent = json.loads(respx_mock.calls[-1].request.content)["params"]
    assert "PriorityGoals" in sent["UnifiedCampaignFieldNames"]
    assert "PriorityGoals" in sent["TextCampaignFieldNames"]


def test_search_finds_write_by_goal_words():
    from directai_mcp.catalog.registry import search

    names = [a.name for a in search("изменить ключевые цели кампании", mode="write")]
    assert "campaigns_update" in names