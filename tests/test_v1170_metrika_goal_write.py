"""v1.17.0: запись целей Метрики (create/update/delete).

Сериализация каждого типа цели, валидация, дубль, право счётчика, лимит
200 целей, полный цикл plan_write -> apply_write -> read-back, таймаут без
повтора и понятная ошибка при отсутствии metrika:write. Только моки:
живые счётчики не трогаются.
"""

import json

import httpx
import pytest

import directai_mcp.catalog.metrika_goal_write as gw
from directai_mcp.api.errors import MetrikaApiError
from directai_mcp.catalog.metrika_goal_write import (
    MAX_GOALS_PER_COUNTER,
    MetrikaGoalDeleteParams,
    MetrikaGoalUpdateParams,
    build_goal_body,
    find_duplicate,
    goal_fingerprint,
)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety.guard import CONFIRM_SUFFIX, DANGER_NOTICE
from directai_mcp.server import PLANS, do_apply_write, do_plan_write

BASE = "https://api-metrika.yandex.net"
COUNTER = 54578446
TOKEN = "secret-token-value"


@pytest.fixture(autouse=True)
def _clean_plans():
    PLANS.clear()
    yield
    PLANS.clear()


def _ctx(tmp_path, mode="confirm"):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        guard_mode=mode,
    )
    return Ctx(settings=settings, token=TOKEN, data_dir=tmp_path)


def _counter(permission="own", counter_id=COUNTER):
    return httpx.Response(200, json={
        "counter": {"id": counter_id, "name": "Счётчик",
                    "permission": permission, "site2": {"site": "example.ru"}}
    })


def _goals(*goals):
    return httpx.Response(200, json={"goals": list(goals)})


def _action(*goals):
    return {"id": 555, "name": "Заявка", "type": "action", "status": "active",
            "conditions": [{"type": "exact", "url": "directai_test_goal"}]}


def _live_goal(goal_id, name="Заявка", **extra):
    goal = {
        "id": goal_id,
        "name": name,
        "type": "action",
        "status": "active",
        "conditions": [{"type": "exact", "url": "lead_submit"}],
    }
    goal.update(extra)
    return goal


def _pid(out: str) -> str:
    assert out.startswith("План "), out
    return out.split()[1].rstrip(":")


def _params(**kw):
    return ACTIONS["metrika_goal_create"].params(**kw)


# -- сериализация типов целей ------------------------------------------------


def test_action_goal_serialization():
    body = build_goal_body(_params(
        counter_id=COUNTER, type="action", name="Заявка",
        conditions=[{"operator": "exact", "value": "lead_submit"}],
        default_price=500.5, is_favorite=True))
    assert body == {
        "name": "Заявка",
        "type": "action",
        "default_price": 500.5,
        "is_favorite": True,
        "conditions": [{"type": "exact", "url": "lead_submit"}],
    }


def test_url_goal_conditions_are_or():
    body = build_goal_body(_params(
        counter_id=COUNTER, type="url", name="Страницы заказа",
        conditions=[{"operator": "contain", "value": "/order/"},
                    {"operator": "regexp", "value": "^/checkout"}]))
    assert body["conditions"] == [
        {"type": "contain", "url": "/order/"},
        {"type": "regexp", "url": "^/checkout"},
    ]


def test_phone_goal_serialization():
    body = build_goal_body(_params(
        counter_id=COUNTER, type="phone", name="Звонок",
        conditions=[{"operator": "exact", "value": "+74951234567"}],
        hide_phone_number=True))
    assert body["hide_phone_number"] is True
    assert body["conditions"] == [
        {"type": "exact", "url": "+74951234567"}
    ]


def test_messenger_email_search_serialization():
    for gtype, value, limit_ok in (
        ("messenger", "https://t.me/durov", True),
        ("email", "sales@example.ru", True),
        ("search", "купить", True),
    ):
        body = build_goal_body(_params(
            counter_id=COUNTER, type=gtype, name="Цель",
            conditions=[{"operator": "exact", "value": value}]))
        assert body["conditions"] == [{"type": "exact", "url": value}], gtype
        assert limit_ok


def test_file_goal_serialization():
    all_files = build_goal_body(_params(
        counter_id=COUNTER, type="file", name="Любой файл", file_all=True))
    assert all_files["conditions"] == [{"type": "all_files"}]
    one = build_goal_body(_params(
        counter_id=COUNTER, type="file", name="Прайс",
        file_url="/files/price.pdf"))
    assert one["conditions"] == [
        {"type": "file", "url": "/files/price.pdf"}
    ]


def test_social_goal_serialization():
    all_social = build_goal_body(_params(
        counter_id=COUNTER, type="social", name="Соцсети", social_all=True))
    assert all_social["conditions"] == [{"type": "all_social"}]
    one = build_goal_body(_params(
        counter_id=COUNTER, type="social", name="ВК", social_url="vk.com/x"))
    assert one["conditions"] == [{"type": "social", "url": "vk.com/x"}]


def test_step_goal_serialization_ordered():
    body = build_goal_body(_params(
        counter_id=COUNTER, type="step", name="Воронка", steps=[
            {"type": "url",
             "conditions": [{"operator": "contain", "value": "/cart"}]},
            {"type": "action",
             "conditions": [{"operator": "exact", "value": "order_done"}]},
        ]))
    assert body["steps"] == [
        {"type": "url", "conditions": [{"type": "contain", "url": "/cart"}]},
        {"type": "action",
         "conditions": [{"type": "exact", "url": "order_done"}]},
    ]


def test_number_and_duration_serialization():
    depth = build_goal_body(_params(
        counter_id=COUNTER, type="number", name="Глубина 3", depth=3))
    assert depth["depth"] == 3 and "conditions" not in depth
    duration = build_goal_body(_params(
        counter_id=COUNTER, type="visit_duration", name="Минута", duration=60))
    assert duration["duration"] == 60


# -- валидация ---------------------------------------------------------------


def test_multi_goal_rejected_with_api_fact():
    with pytest.raises(ValueError) as err:
        _params(counter_id=COUNTER, type="multi", name="Мульти",
                conditions=[{"operator": "exact", "value": "a"}])
    assert "Мультицели в Management API нет" in str(err.value)
    assert "ИЛИ" in str(err.value)


def test_unknown_type_rejected():
    with pytest.raises(ValueError, match="неизвестен"):
        _params(counter_id=COUNTER, type="call", name="Звонок")


@pytest.mark.parametrize("gtype,field", [
    ("action", "conditions"),
    ("email", "conditions"),
    ("messenger", "conditions"),
    ("phone", "conditions"),
    ("search", "conditions"),
])
def test_conditions_required(gtype, field):
    with pytest.raises(ValueError, match="нужен хотя бы один"):
        _params(counter_id=COUNTER, type=gtype, name="Цель")


def test_bad_operator_rejected():
    with pytest.raises(ValueError, match="оператор"):
        _params(counter_id=COUNTER, type="action", name="Цель",
                conditions=[{"operator": "starts", "value": "x"}])


def test_empty_and_long_value_rejected():
    with pytest.raises(ValueError, match="пустое"):
        _params(counter_id=COUNTER, type="action", name="Цель",
                conditions=[{"operator": "exact", "value": "  "}])
    with pytest.raises(ValueError, match="символов"):
        _params(counter_id=COUNTER, type="email", name="Цель",
                conditions=[{"operator": "exact", "value": "a" * 1025}])


def test_steps_limits_and_types():
    one = [{"type": "action", "conditions": [
        {"operator": "exact", "value": "a"}]}]
    with pytest.raises(ValueError, match="шагов 1"):
        _params(counter_id=COUNTER, type="step", name="Ф", steps=one)
    with pytest.raises(ValueError, match="шагов 6"):
        _params(counter_id=COUNTER, type="step", name="Ф", steps=one * 6)
    # Тип шага вне action|url отклоняется моделью (Literal) до валидатора.
    with pytest.raises(ValueError):
        _params(counter_id=COUNTER, type="step", name="Ф", steps=[
            {"type": "phone", "conditions": [
                {"operator": "exact", "value": "+7"}]}] * 2)


def test_depth_duration_limits():
    with pytest.raises(ValueError, match="минимум 2"):
        _params(counter_id=COUNTER, type="number", name="Глубина", depth=1)
    with pytest.raises(ValueError, match="минимум 1"):
        _params(counter_id=COUNTER, type="visit_duration", name="Визит",
                duration=0)


def test_file_social_exactly_one_flag():
    with pytest.raises(ValueError, match="ровно одно"):
        _params(counter_id=COUNTER, type="file", name="Файлы")
    with pytest.raises(ValueError, match="ровно одно"):
        _params(counter_id=COUNTER, type="file", name="Файлы",
                file_all=True, file_url="/a.pdf")
    with pytest.raises(ValueError, match="ровно одно"):
        _params(counter_id=COUNTER, type="social", name="Соцсети")


def test_name_and_price_validation():
    with pytest.raises(ValueError, match="пустое"):
        _params(counter_id=COUNTER, type="number", name=" ", depth=2)
    with pytest.raises(ValueError, match="длиннее 255"):
        _params(counter_id=COUNTER, type="number", name="я" * 256, depth=2)
    with pytest.raises(ValueError, match="отрицательным"):
        _params(counter_id=COUNTER, type="number", name="Глубина", depth=2,
                default_price=-1)


def test_foreign_params_rejected():
    with pytest.raises(ValueError, match="не применим"):
        _params(counter_id=COUNTER, type="number", name="Глубина", depth=2,
                conditions=[{"operator": "exact", "value": "a"}])
    with pytest.raises(ValueError, match="не применим"):
        _params(counter_id=COUNTER, type="action", name="Событие",
                conditions=[{"operator": "exact", "value": "a"}], depth=3)


def test_update_requires_change():
    with pytest.raises(ValueError, match="нечего менять"):
        MetrikaGoalUpdateParams(counter_id=COUNTER, goal_id=1)


def test_counter_id_required():
    with pytest.raises(ValueError):
        MetrikaGoalDeleteParams(goal_id=1)


# -- дубль и отпечаток цели --------------------------------------------------


def test_fingerprint_ignores_order_and_spaces():
    left = {"id": 1, "type": "url", "conditions": [
        {"type": "exact", "url": "/a"}, {"type": "contain", "url": "/b"}]}
    right = {"id": 2, "type": "url", "conditions": [
        {"type": "contain", "url": " /b "}, {"type": "exact", "url": "/a"}]}
    assert goal_fingerprint(left) == goal_fingerprint(right)


def test_fingerprint_step_order_matters():
    left = {"id": 1, "type": "step", "steps": [
        {"type": "action", "conditions": [{"type": "exact", "url": "a"}]},
        {"type": "url", "conditions": [{"type": "exact", "url": "b"}]}]}
    right = {"id": 2, "type": "step", "steps": list(reversed(left["steps"]))}
    assert goal_fingerprint(left) != goal_fingerprint(right)


def test_fingerprint_number_and_duration():
    assert goal_fingerprint({"type": "number", "depth": 3}) == (
        "number", 3)
    assert goal_fingerprint(
        {"type": "visit_duration", "duration": 60}) == ("visit_duration", 60)


def test_find_duplicate_matches_type_and_conditions():
    goals = [_live_goal(11, name="Старая"), {"id": 12, "type": "number",
                                            "depth": 3}]
    wanted = build_goal_body(_params(
        counter_id=COUNTER, type="action", name="Новая",
        conditions=[{"operator": "exact", "value": " lead_submit "}]))
    assert find_duplicate(goals, wanted)["id"] == 11
    other = build_goal_body(_params(
        counter_id=COUNTER, type="action", name="Другая",
        conditions=[{"operator": "exact", "value": "other_event"}]))
    assert find_duplicate(goals, other) is None
    assert find_duplicate(
        goals, {"type": "number", "depth": 3})["id"] == 12


# -- prepare: право счётчика, дубль, лимит, объект не найден -----------------


async def test_prepare_refuses_view_only_counter(respx_mock, tmp_path):
    route = respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter("view"))
    with pytest.raises(ValueError, match="только просмотр"):
        await gw._prepare_create(_ctx(tmp_path), None, _params(
            counter_id=COUNTER, type="action", name="Цель",
            conditions=[{"operator": "exact", "value": "e"}]))
    assert not route.called


async def test_prepare_refuses_edit_permission_missing(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=httpx.Response(200, json={"counter": {"id": COUNTER}}))
    with pytest.raises(ValueError, match="не удалось подтвердить право"):
        await gw._prepare_create(_ctx(tmp_path), None, _params(
            counter_id=COUNTER, type="action", name="Цель",
            conditions=[{"operator": "exact", "value": "e"}]))


async def test_prepare_refuses_duplicate(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter("edit"))
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(42, name="Уже есть")))
    with pytest.raises(ValueError, match="дубль") as err:
        await gw._prepare_create(_ctx(tmp_path), None, _params(
            counter_id=COUNTER, type="action", name="Новая",
            conditions=[{"operator": "exact", "value": "lead_submit"}]))
    assert "id 42" in str(err.value)
    assert "Уже есть" in str(err.value)


async def test_prepare_refuses_over_limit(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    many = [_live_goal(i, name=f"Цель {i}") for i in range(
        MAX_GOALS_PER_COUNTER)]
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(*many))
    with pytest.raises(ValueError, match="лимит"):
        await gw._prepare_create(_ctx(tmp_path), None, _params(
            counter_id=COUNTER, type="action", name="Новая",
            conditions=[{"operator": "exact", "value": "brand_new_event"}]))


async def test_prepare_update_requires_live_goal(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(1)))
    with pytest.raises(ValueError, match="не найдена"):
        await gw._prepare_update(_ctx(tmp_path), None,
                                 MetrikaGoalUpdateParams(
                                     counter_id=COUNTER, goal_id=99,
                                     name="Новое"))


async def test_prepare_update_refuses_same_values(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(1, name="Заявка")))
    with pytest.raises(ValueError, match="не требуется"):
        await gw._prepare_update(_ctx(tmp_path), None,
                                 MetrikaGoalUpdateParams(
                                     counter_id=COUNTER, goal_id=1,
                                     name="Заявка"))


async def test_prepare_update_keeps_untouched_fields(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(
            7, name="Заявка", default_price=100.0, is_favorite=True)))
    prep = await gw._prepare_update(
        _ctx(tmp_path), None,
        MetrikaGoalUpdateParams(counter_id=COUNTER, goal_id=7, name="Лид"))
    body = prep["requests"][0][2]["goal"]
    assert body["id"] == 7
    assert body["name"] == "Лид"
    assert body["default_price"] == 100.0
    assert body["is_favorite"] is True
    assert body["conditions"] == [{"type": "exact", "url": "lead_submit"}]
    assert prep["warnings"]


# -- полный цикл: plan_write -> apply_write -> read-back ---------------------


async def test_full_cycle_create(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    goals = respx_mock.get(
        f"{BASE}/management/v1/counter/{COUNTER}/goals")
    goals.mock(return_value=_goals(_live_goal(1, name="Другая")))
    post = respx_mock.post(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    post.mock(return_value=httpx.Response(
        200, json={"goal": _action()}))
    out = await do_plan_write(ctx, "metrika_goal_create", {
        "counter_id": COUNTER, "type": "action", "name": "Заявка",
        "conditions": [{"operator": "exact", "value": "directai_test_goal"}]})
    pid = _pid(out)
    assert "будет создана" in out
    assert "нужно подтверждение" not in out
    goals.mock(return_value=_goals(
        _live_goal(1, name="Другая"), _action()))
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    sent = json.loads(post.calls[-1].request.content)["goal"]
    assert sent["conditions"] == [
        {"type": "exact", "url": "directai_test_goal"}]
    assert "статус applied" in applied, applied
    assert "цель 555" in applied
    assert "подтверждено read-back" in applied
    assert "Запись журнала #" in applied
    again = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "уже применён" in again


async def test_create_warnings_need_acknowledge(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals())
    out = await do_plan_write(ctx, "metrika_goal_create", {
        "counter_id": COUNTER, "type": "number", "name": "Глубина 3",
        "depth": 3})
    pid = _pid(out)
    applied = await do_apply_write(ctx, pid)
    assert "acknowledge_warnings=true" in applied


async def test_full_cycle_update(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    goals = respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    goals.mock(return_value=_goals(_live_goal(7, name="Старое")))
    put = respx_mock.put(
        f"{BASE}/management/v1/counter/{COUNTER}/goal/7")
    out = await do_plan_write(ctx, "metrika_goal_update", {
        "counter_id": COUNTER, "goal_id": 7, "name": "Новое",
        "conditions": [{"operator": "contain", "value": "form_submit"}]})
    pid = _pid(out)
    assert "Изменения:" in out and "имя: «Старое» → «Новое»" in out
    put.mock(return_value=httpx.Response(200, json={"goal": {
        "id": 7, "name": "Новое", "type": "action",
        "conditions": [{"type": "contain", "url": "form_submit"}]}}))
    goals.mock(return_value=_goals(_live_goal(
        7, name="Новое", conditions=[{"type": "contain",
                                        "url": "form_submit"}])))
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус applied" in applied, applied
    assert "имя, тип, условия и цена совпали" in applied
    body = json.loads(put.calls[-1].request.content)["goal"]
    assert body == {
        "id": 7, "name": "Новое", "type": "action",
        "conditions": [{"type": "contain", "url": "form_submit"}]}


async def test_update_readback_detects_mismatch(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    goals = respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    goals.mock(return_value=_goals(_live_goal(7, name="Старое")))
    respx_mock.put(f"{BASE}/management/v1/counter/{COUNTER}/goal/7").mock(
        return_value=httpx.Response(200, json={"goal": {"id": 7}}))
    out = await do_plan_write(ctx, "metrika_goal_update", {
        "counter_id": COUNTER, "goal_id": 7, "name": "Новое"})
    pid = _pid(out)
    goals.mock(return_value=_goals(_live_goal(7, name="Старое")))
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус unverified" in applied, applied
    assert "НЕ подтвердил" in applied


# -- удаление: owner_confirmed + необратимость ------------------------------


async def test_delete_requires_owner_confirm(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(7, name="Заявка")))
    out = await do_plan_write(ctx, "metrika_goal_delete", {
        "counter_id": COUNTER, "goal_id": 7})
    pid = _pid(out)
    assert DANGER_NOTICE in out
    assert "необратимо" in out
    assert "удаляется навсегда" in out
    blocked = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "ОПАСНАЯ ОПЕРАЦИЯ" in blocked
    assert "owner_confirmed=true" in blocked
    plan = PLANS.peek(pid)
    assert plan.danger == [
        (
            "удаление цели Метрики 7 («Заявка») — необратимо: собранная по "
            "цели статистика пропадёт из отчётов (справка Метрики) — "
            f"{CONFIRM_SUFFIX}"
        )
    ]


async def test_full_cycle_delete(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    goals = respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    goals.mock(return_value=_goals(_live_goal(7, name="Заявка")))
    delete = respx_mock.delete(
        f"{BASE}/management/v1/counter/{COUNTER}/goal/7")
    delete.mock(return_value=httpx.Response(200, json={"success": True}))
    out = await do_plan_write(ctx, "metrika_goal_delete", {
        "counter_id": COUNTER, "goal_id": 7})
    pid = _pid(out)
    goals.mock(return_value=_goals())
    applied = await do_apply_write(
        ctx, pid, acknowledge_warnings=True, owner_confirmed=True)
    assert "статус applied" in applied, applied
    assert "цели 7 в счётчике 54578446 нет" in applied


async def test_delete_without_confirmation_in_block_mode(
    respx_mock, tmp_path
):
    """Даже в режиме block удаление требует owner_confirmed, а не блока."""
    ctx = _ctx(tmp_path, mode="block")
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(7)))
    out = await do_plan_write(ctx, "metrika_goal_delete", {
        "counter_id": COUNTER, "goal_id": 7})
    pid = _pid(out)
    assert "Заблокировано защитой" not in out
    assert "ОПАСНАЯ ОПЕРАЦИЯ" in out
    blocked = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "owner_confirmed=true" in blocked


async def test_delete_prepare_outside_plan_is_blocked(respx_mock, tmp_path):
    """Вне сбора причин (прямой вызов prepare) удаление не проходит."""
    from directai_mcp.safety.guard import GuardBlocked

    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=_goals(_live_goal(7)))
    with pytest.raises(GuardBlocked):
        await gw._prepare_delete(
            _ctx(tmp_path), None,
            MetrikaGoalDeleteParams(counter_id=COUNTER, goal_id=7))


# -- таймаут: без повтора вслепую -------------------------------------------


async def test_create_timeout_is_unverified_and_not_retried(
    respx_mock, tmp_path
):
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    goals = respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    goals.mock(return_value=_goals(_live_goal(1, name="Другая")))
    post = respx_mock.post(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    post.mock(side_effect=httpx.ReadTimeout("timeout"))
    out = await do_plan_write(ctx, "metrika_goal_create", {
        "counter_id": COUNTER, "type": "action", "name": "Заявка",
        "conditions": [{"operator": "exact", "value": "directai_test_goal"}]})
    pid = _pid(out)
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус unverified" in applied, applied
    assert "НЕ повторяем вслепую" in applied
    assert post.call_count == 1
    # read-back после таймаута состоялся: цели прочитаны повторно (2 GET).
    assert goals.call_count == 2


async def test_create_timeout_resolved_by_readback(respx_mock, tmp_path):
    """Таймаут, но цель создалась: read-back это показывает, повтора нет."""
    ctx = _ctx(tmp_path)
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=_counter())
    goals = respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    goals.mock(return_value=_goals(_live_goal(1, name="Другая")))
    post = respx_mock.post(f"{BASE}/management/v1/counter/{COUNTER}/goals")
    post.mock(side_effect=httpx.ReadTimeout("timeout"))
    out = await do_plan_write(ctx, "metrika_goal_create", {
        "counter_id": COUNTER, "type": "action", "name": "Заявка",
        "conditions": [{"operator": "exact", "value": "directai_test_goal"}]})
    pid = _pid(out)
    goals.mock(return_value=_goals(
        _live_goal(1, name="Другая"), _action()))
    applied = await do_apply_write(ctx, pid, acknowledge_warnings=True)
    assert "статус unverified" in applied, applied
    assert "подтверждено read-back" in applied
    assert post.call_count == 1


# -- права токена metrika:write ---------------------------------------------


async def test_403_explains_metrika_write_scope(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}").mock(
        return_value=httpx.Response(
            403, json={"error": "forbidden",
                       "message": "Not enough permissions"}))
    with pytest.raises(ValueError) as err:
        await gw._prepare_create(_ctx(tmp_path), None, _params(
            counter_id=COUNTER, type="number", name="Глубина", depth=2))
    text = str(err.value)
    assert "metrika:write" in text
    assert "set-token --login" in text
    assert TOKEN not in text


async def test_401_hint_no_token_in_text(respx_mock, tmp_path):
    respx_mock.get(f"{BASE}/management/v1/counter/{COUNTER}/goals").mock(
        return_value=httpx.Response(401, text="Unauthorized"))
    with pytest.raises(ValueError) as err:
        await gw._goals(TOKEN, COUNTER)
    assert "set-token --login" in str(err.value)
    assert TOKEN not in str(err.value)


def test_metrika_error_unverified_flag():
    plain = MetrikaApiError("boom", status=500)
    timeout = MetrikaApiError("таймаут", unverified=True)
    assert plain.unverified is False
    assert timeout.unverified is True


# -- регистрация и поиск -----------------------------------------------------


def test_actions_registered_as_write():
    for name in ("metrika_goal_create", "metrika_goal_update",
                 "metrika_goal_delete"):
        act = ACTIONS[name]
        assert act.mode == "write"
        assert act.prepare and act.apply and act.verify


def test_search_finds_goal_write():
    from directai_mcp.catalog.registry import search

    assert "metrika_goal_create" in [
        a.name for a in search("создать цель метрики", mode="write")
    ]
    assert "metrika_goal_delete" in [
        a.name for a in search("удалить цель", mode="write")
    ]