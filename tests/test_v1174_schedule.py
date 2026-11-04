"""v1.17.4: расписание показов (TimeTargeting.Schedule) в campaigns_update.

Сериализация «дни + часы» -> Items API, валидация, сохранение
ConsiderWorkingWeekends/HolidaysSchedule, превью «было → стало»,
read-back и полный цикл plan_write. Только моки.
"""

from types import SimpleNamespace

import httpx
import pytest

import directai_mcp.catalog.campaigns as _cm  # noqa: F401 (реестр)
from directai_mcp.catalog.campaigns import (
    ScheduleSpec,
    _fmt_schedule,
    _prepare_campaigns_update,
    _schedule_hours,
    _schedule_items,
    _schedule_slots,
    _verify_campaigns_updated,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety.guard import combat_allowed
from directai_mcp.server import PLANS, do_plan_write

V501C = "https://api.direct.yandex.com/json/v501/campaigns"
TEST_ID = 700008
TEST_NAME = "[TEST DirectAI] Расписание"


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _entry():
    return AccountEntry(alias="m", login="agency-login")


def _ok(result):
    return httpx.Response(200, json={"result": result})


def _day(num, hot_hours=()):
    hours = [100 if h in hot_hours else 0 for h in range(24)]
    return ",".join([str(num)] + [str(h) for h in hours])


def _tt():
    return {
        "Schedule": {"Items": [_day(d, range(10, 19)) for d in (1, 2, 3, 4, 5)]
                     + [_day(6), _day(7)]},
        "ConsiderWorkingWeekends": "YES",
        "HolidaysSchedule": {"SuspendOnHolidays": "YES"},
    }


def _campaign(**extra):
    return _ok({"Campaigns": [{
        "Id": TEST_ID, "Name": TEST_NAME, "Type": "TEXT_CAMPAIGN",
        "TimeTargeting": _tt(), **extra}]})


def _params(**kw):
    return ACTIONS["campaigns_update"].params(
        account="m", campaign_ids=[TEST_ID], **kw)


# -- сериализация ------------------------------------------------------------


def test_items_roundtrip_to_display():
    items = _schedule_items([1, 2, 3, 4, 5], list(range(10, 19)))
    assert len(items) == 7  # все дни, пустые — нули
    assert _fmt_schedule({"Schedule": {"Items": items}}) == \
        "Пн–Пт 10:00–19:00 (100%); Сб–Вс 0%"
    assert len(_schedule_slots({"Schedule": {"Items": items}})) == 45


def test_interval_and_hours_are_equivalent():
    by_interval = ScheduleSpec(days=[1], from_hour=10, to_hour=19)
    by_hours = ScheduleSpec(days=[1], hours=list(range(10, 19)))
    assert _schedule_hours(by_interval) == _schedule_hours(by_hours)
    assert _schedule_hours(by_interval) == list(range(10, 19))
    # Дамп модели (params плана) читается так же.
    assert _schedule_hours(by_interval.model_dump()) == list(range(10, 19))


@pytest.mark.parametrize("kw, match", [
    ({"days": [1, 1], "hours": [10]}, "дни повторяются"),
    ({"days": [8], "hours": [10]}, "вне 1..7"),
    ({"days": [1], "hours": [10], "from_hour": 9, "to_hour": 18},
     "либо hours"),
    ({"days": [1]}, "нужны hours"),
    ({"days": [1], "from_hour": 19, "to_hour": 10}, "меньше to_hour"),
    ({"days": [1], "hours": [24]}, "вне 0..23"),
    ({"days": [1], "hours": [10, 10]}, "часы повторяются"),
])
def test_spec_validation(kw, match):
    with pytest.raises(ValueError, match=match):
        ScheduleSpec(**kw)


# -- prepare -----------------------------------------------------------------


async def test_prepare_writes_schedule(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_campaign())
    prep = await _prepare_campaigns_update(
        _ctx(tmp_path), _entry(),
        _params(schedule={"days": [1, 2, 3, 4, 5], "from_hour": 9, "to_hour": 18}))
    body = prep["requests"][0][2]["Campaigns"][0]
    assert body["TimeTargeting"] == {
        "Schedule": {"Items": _schedule_items([1, 2, 3, 4, 5],
                                             list(range(9, 18)))},
        "ConsiderWorkingWeekends": "YES",
        "HolidaysSchedule": {"SuspendOnHolidays": "YES"},
    }
    assert "расписание: Пн–Пт 10:00–19:00 (100%); Сб–Вс 0% → " \
           "Пн–Пт 09:00–18:00 (100%); Сб–Вс 0%" in prep["preview"]
    # Побочных полей нет: кампания не перезапускается.
    assert set(body) == {"Id", "TimeTargeting"}


async def test_prepare_schedule_off(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_campaign())
    prep = await _prepare_campaigns_update(
        _ctx(tmp_path), _entry(), _params(schedule_off=True))
    body = prep["requests"][0][2]["Campaigns"][0]
    items = body["TimeTargeting"]["Schedule"]["Items"]
    assert items == _schedule_items(list(range(1, 8)), list(range(24)))
    assert "Пн–Вс 00:00–24:00 (100%)" in prep["preview"]


async def test_prepare_rejects_both(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_campaign())
    with pytest.raises(ValueError, match="взаимоисключающие"):
        await _prepare_campaigns_update(
            _ctx(tmp_path), _entry(),
            _params(schedule={"days": [1], "hours": [10]}, schedule_off=True))


# -- read-back ---------------------------------------------------------------


def _plan(params, version="v5"):
    return SimpleNamespace(
        params=params,
        requests=[("campaigns", "update", {"Campaigns": [{"Id": TEST_ID}]},
                   version)])


async def test_verify_schedule_ok(respx_mock, tmp_path):
    items = _schedule_items([1, 2], [10, 11])
    respx_mock.post(V501C.replace("/v501", "/v5")).mock(return_value=_ok({
        "Campaigns": [{"Id": TEST_ID,
                       "TimeTargeting": {"Schedule": {"Items": items}}}]}))
    result = await _verify_campaigns_updated(
        _ctx(tmp_path), _entry(),
        _plan({"campaign_ids": [TEST_ID],
               "schedule": {"days": [1, 2], "hours": [10, 11]}}))
    assert result["ok"] is True
    assert result["note"] == "подтверждено read-back."


async def test_verify_schedule_mismatch(respx_mock, tmp_path):
    respx_mock.post(V501C.replace("/v501", "/v5")).mock(return_value=_ok({
        "Campaigns": [{"Id": TEST_ID, "TimeTargeting": _tt()}]}))
    result = await _verify_campaigns_updated(
        _ctx(tmp_path), _entry(),
        _plan({"campaign_ids": [TEST_ID],
               "schedule": {"days": [6], "hours": [10]}}))
    assert result["ok"] is False
    assert f"{TEST_ID}.schedule" in result["note"]


async def test_verify_schedule_off_without_schedule_in_answer(
        respx_mock, tmp_path):
    # «Круглосуточно» API может отдать без Schedule — это не расхождение.
    respx_mock.post(V501C.replace("/v501", "/v5")).mock(return_value=_ok({
        "Campaigns": [{"Id": TEST_ID,
                       "TimeTargeting": {"ConsiderWorkingWeekends": "NO"}}]}))
    result = await _verify_campaigns_updated(
        _ctx(tmp_path), _entry(),
        _plan({"campaign_ids": [TEST_ID], "schedule_off": True}))
    assert result["ok"] is True


# -- guard и план ------------------------------------------------------------


def test_guard_allows_schedule_in_combat():
    base = {"campaign_ids": [1]}
    assert combat_allowed("campaigns_update",
                          {**base, "schedule": {"days": [1]}}) is True
    assert combat_allowed("campaigns_update",
                          {**base, "schedule_off": True}) is True
    assert combat_allowed("campaigns_update", base) is False
    assert combat_allowed("campaigns_update", {
        **base, "schedule": {"days": [1]}, "strategy": {"a": 1}}) is False


async def test_plan_write_preview(respx_mock, tmp_path):
    respx_mock.post(V501C).mock(return_value=_campaign())
    out = await do_plan_write(_ctx(tmp_path), "campaigns_update", {
        "account": "m", "campaign_ids": [TEST_ID],
        "schedule": {"days": [1, 2, 3, 4, 5], "hours": list(range(9, 18))}})
    assert out.startswith("План "), out
    assert "расписание:" in out and "09:00–18:00" in out
