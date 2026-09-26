"""v1.2.1: итог = Σ строк той же популяции; смешение key/LC при account=all."""

import httpx
import respx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

V501 = "https://api.direct.yandex.com/json/v501/campaigns"
RURL = "https://api.direct.yandex.com/json/v5/reports"

AGENCY = "agency-login"
BARE = "client-b"


def _ctx(tmp_path):
    import datetime as _dt
    import json as _json

    fresh = _dt.datetime.now(_dt.UTC).isoformat()
    (tmp_path / "accounts_cache.json").write_text(_json.dumps({
        "updated_at": fresh,
        "method": "Clients.get",
        "manager": AGENCY,
        "logins": [AGENCY, BARE],
        "checks": {AGENCY: {"on": 1}, BARE: {"on": 0}},
    }), encoding="utf-8")
    settings = Settings(
        auth_login=AGENCY,
        accounts={
            "a": AccountEntry(alias="a", login=AGENCY),
            "b": AccountEntry(alias="b", login=BARE),
        },
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


def _camp(login):
    goals = [{"GoalId": 9}] if login == AGENCY else []
    return httpx.Response(200, json={"result": {"Campaigns": [{
        "Id": 7, "Type": "UNIFIED_CAMPAIGN",
        "UnifiedCampaign": {
            "PriorityGoals": {"Items": goals},
            "BiddingStrategy": {},
        },
    }]}})


def _route_v501(request):
    return _camp(request.headers.get("Client-Login", ""))


ROW_KEY = "Impressions\tClicks\tCost\tConversions_9_AUTO\n100\t10\t1100.00\t5\n"
AGG_KEY = "Impressions\tClicks\tCost\tConversions\n100\t10\t1100.00\t7\n"
ROW_BARE = "Impressions\tClicks\tCost\tConversions\n100\t10\t600.00\t3\n"
AGG_BARE = "Impressions\tClicks\tCost\tConversions\n100\t10\t600.00\t4\n"


def _route_reports(request):
    import json as _json

    params = _json.loads(request.content)["params"]
    login = request.headers.get("Client-Login", "")
    goals = params.get("Goals")
    if login == AGENCY:
        return _tsv(ROW_KEY if goals else AGG_KEY)
    return _tsv(ROW_BARE if goals else AGG_BARE)


async def _run_summary(tmp_path):
    ctx = _ctx(tmp_path)
    with respx.mock:
        respx.post(V501).mock(side_effect=_route_v501)
        respx.post(RURL).mock(side_effect=_route_reports)
        out = await ACTIONS["stats_summary"].run(
            ctx, ACTIONS["stats_summary"].params(account="all"))
    return out


async def test_mix_totals_equal_key_rows(respx_mock, tmp_path):
    out = await _run_summary(tmp_path)
    # Шапка: фактические модели покампанийно (поимённо, кабинетов ≤ 5).
    assert "атрибуция: AUTO (кабинеты: agency-login)" in out
    assert "LC без целей (кабинеты: client-b)" in out
    assert "кабинет agency-login: 1 цель" in out
    assert "кабинет client-b: без ключевых целей" in out
    # Строка беcцельного кабинета — как есть (все цели, LC).
    assert "client-b" in out
    # Итог key-популяции == Σ key-строк: конверсии 5.
    assert "Итого по key-целям (AUTO), 1 кабинет" in out
    assert "Конверсии: 5;" in out
    # CPA key-итога == Σ Cost key-строк / Σ Conv key-строк (1100/5).
    assert "CPA: 220.00 ₽" in out
    assert "CR: 50.00%" in out
    # Деньги всех кабинетов — отдельной строкой без конверсий.
    assert ("Итого (все кабинеты): Показы: 200; Клики: 20; CTR: 10.00%; "
            "Расход: 1 700.00 ₽; CPC: 85.00 ₽.") in out
    # Агрегат без целей — отдельной метрикой (7 + 4).
    assert ("Конверсии (все цели, LC): 11 "
            "(другая популяция, не итог).") in out


async def test_mix_rows_marked_with_model(respx_mock, tmp_path):
    out = await _run_summary(tmp_path)
    # v1.2.2: пометка строк прямо в таблице.
    assert "| Модель |" in out
    assert "| 1 | agency-login | AUTO |" in out
    rows = [ln for ln in out.splitlines() if ln.startswith("|")]
    key_row = next(ln for ln in rows if "agency-login" in ln)
    bare_row = next(ln for ln in rows if "client-b" in ln)
    assert "| AUTO |" in key_row
    assert "все цели (LC)" in bare_row


async def test_mix_dimensional_totals(respx_mock, tmp_path):
    ctx = _ctx(tmp_path)
    with respx.mock:
        respx.post(V501).mock(side_effect=_route_v501)
        respx.post(RURL).mock(side_effect=_route_reports)
        out = await ACTIONS["stats_campaigns"].run(
            ctx, ACTIONS["stats_campaigns"].params(account="all"))
    assert "Итого по key-целям (AUTO), 1 кабинет" in out
    assert "Конверсии: 5;" in out
    assert "CPA: 220.00 ₽" in out
    assert "Итого (все кабинеты):" in out
    assert "Конверсии (все цели, LC): 11" in out


async def test_single_explicit_goal_total_is_row_sum(respx_mock, tmp_path):
    settings = Settings(
        auth_login=AGENCY,
        accounts={"a": AccountEntry(alias="a", login=AGENCY)},
        accounts_path=tmp_path / "accounts.toml",
    )
    ctx = Ctx(settings=settings, token="t", data_dir=tmp_path)
    row = "CampaignId\tConversions_9_AUTO\n7\t5\n"
    agg = "CampaignId\tConversions\n7\t8\n"
    with respx.mock:
        respx.post(RURL).mock(side_effect=[_tsv(row), _tsv(agg)])
        out = await ACTIONS["stats_campaigns"].run(
            ctx, ACTIONS["stats_campaigns"].params(
                account="agency-login", campaign_ids=[7], goals=["9"],
                attribution=["AUTO"]))
    assert "Конверсии: 5;" in out
    assert "Конверсии (все цели, LC): 8 (другая популяция, не итог)." in out
    assert "LC без целей" not in out


async def test_many_cabinets_header_without_logins(respx_mock, tmp_path):
    import datetime as _dt
    import json as _json

    logins = [AGENCY, BARE] + [f"demo-login-{i}" for i in range(1, 6)]
    fresh = _dt.datetime.now(_dt.UTC).isoformat()
    (tmp_path / "accounts_cache.json").write_text(_json.dumps({
        "updated_at": fresh,
        "method": "Clients.get",
        "manager": AGENCY,
        "logins": logins,
        "checks": {},
    }), encoding="utf-8")
    settings = Settings(
        auth_login=AGENCY,
        accounts={f"a{i}": AccountEntry(alias=f"a{i}", login=lg)
                  for i, lg in enumerate(logins)},
        accounts_path=tmp_path / "accounts.toml",
    )
    ctx = Ctx(settings=settings, token="t", data_dir=tmp_path)

    def _route7(request):
        return _camp(request.headers.get("Client-Login", ""))

    row = "Impressions\tClicks\tCost\tConversions_9_AUTO\n100\t10\t1100.00\t5\n"
    agg = "Impressions\tClicks\tCost\tConversions\n100\t10\t1100.00\t9\n"

    def _route_r(request):
        import json as _js

        login = request.headers.get("Client-Login", "")
        if login == AGENCY:
            params = _js.loads(request.content)["params"]
            return _tsv(row if params.get("Goals") else agg)
        body = "Impressions\tClicks\tCost\tConversions\n100\t10\t600.00\t3\n"
        return _tsv(body)

    with respx.mock:
        respx.post(V501).mock(side_effect=_route7)
        respx.post(RURL).mock(side_effect=_route_r)
        out = await ACTIONS["stats_summary"].run(
            ctx, ACTIONS["stats_summary"].params(account="all"))
    head = out.splitlines()[0]
    # v1.2.2: при 7 кабинетах — только счётчики, без логинов.
    assert "AUTO: 1 кабинет; LC без целей: 6 кабинетов" in head
    assert "client-b" not in head
    assert "demo-login-" not in head
    assert "кабинет agency-login:" not in head
    assert "Итого по key-целям (AUTO), 1 кабинет" in out
