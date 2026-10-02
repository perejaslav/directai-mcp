"""v1.12.1 Ч2: metrika_direct_cpa — связка Директ + Метрика.

Фикстуры (вымышленные числа), без сети: join по ID, CPA/CR-математика,
флаг расхождения >30%, несопоставленные блоки, 403, пустой ответ.
"""

import directai_mcp.catalog.metrika_cpa as _cpa
from directai_mcp.api.reports import ReportsClient
from directai_mcp.catalog import metrika_goals as _mg
from directai_mcp.catalog.metrika_goals import MetrikaError
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings


def _ctx(tmp_path, **kw):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        **kw,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _fake_direct(monkeypatch, rows):
    async def _fetch(self, login, definition):
        return {}, rows

    monkeypatch.setattr(ReportsClient, "fetch", _fetch)


def _fake_metrika(monkeypatch, goal_names=None):
    _mg.COUNTER_GOALS_CACHE.clear()
    _mg.COUNTER_INFO_CACHE.clear()
    _mg.COUNTER_GOAL_NAMES_CACHE.clear()
    _mg.COUNTER_GOAL_NAMES_CACHE[11] = dict(goal_names or {"5": "Заявка"})
    _mg.COUNTER_GOALS_CACHE[11] = {"5": "action"}


def _metrika_payload(rows):
    return {
        "sampled": False,
        "data": [
            {
                "dimensions": [{"id": oid, "name": name}],
                "metrics": [visits, bounce, goals],
            }
            for oid, name, visits, bounce, goals in rows
        ],
    }


def _params(**kw):
    base = {
        "account": "agency-login",
        "counter_id": 11,
        "date_from": "2026-09-11",
        "date_to": "2026-09-24",
        "goal_ids": ["5"],
    }
    base.update(kw)
    return ACTIONS["metrika_direct_cpa"].params(**base)


async def test_join_and_math(tmp_path, monkeypatch):
    _fake_direct(monkeypatch, [
        {"CampaignId": "7", "CampaignName": "K7", "Clicks": "100", "Cost": "900.00"},
        {"CampaignId": "8", "CampaignName": "K8", "Clicks": "200", "Cost": "500.00"},
    ])
    _fake_metrika(monkeypatch)

    async def _stat(*a, **k):
        return _metrika_payload([
            ("7", "K7", 90.0, 20.0, 9.0),
            ("8", "K8", 200.0, 30.0, 5.0),
        ])

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    # K7: CPA=900/9=100.00, CR=9/90=10%, клики→визиты 90%.
    assert "100,00" in out or "100.00" in out
    assert "K7 (7)" in out and "K8 (8)" in out
    assert "90.0%" in out
    # Сортировка по расходу: K7 выше K8.
    assert out.index("K7 (7)") < out.index("K8 (8)")


async def test_divergence_flag(tmp_path, monkeypatch):
    _fake_direct(monkeypatch, [
        {"CampaignId": "7", "CampaignName": "K7", "Clicks": "100", "Cost": "100.00"},
    ])
    _fake_metrika(monkeypatch)

    async def _stat(*a, **k):
        return _metrika_payload([("7", "K7", 50.0, 10.0, 5.0)])

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    assert "проверить разметку/счётчик" in out


async def test_no_flag_within_threshold(tmp_path, monkeypatch):
    _fake_direct(monkeypatch, [
        {"CampaignId": "7", "CampaignName": "K7", "Clicks": "100", "Cost": "100.00"},
    ])
    _fake_metrika(monkeypatch)

    async def _stat(*a, **k):
        return _metrika_payload([("7", "K7", 85.0, 10.0, 5.0)])

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    assert "проверить разметку/счётчик" not in out


async def test_unmatched_blocks(tmp_path, monkeypatch):
    _fake_direct(monkeypatch, [
        {"CampaignId": "7", "CampaignName": "K7", "Clicks": "100", "Cost": "100.00"},
    ])
    _fake_metrika(monkeypatch)

    async def _stat(*a, **k):
        return _metrika_payload([
            ("999", "Чужая", 10.0, 10.0, 1.0),
            ("other", "Чужие кампании", 33.0, 10.0, 0.0),
        ])

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    assert "Без визитов в Метрике" in out and "K7 (7)" in out
    assert "без кампании в логине" in out and "Чужая (999)" in out
    assert "Чужие кампании" in out and "33 визитов" in out


async def test_zero_goals_cpa_dash(tmp_path, monkeypatch):
    _fake_direct(monkeypatch, [
        {"CampaignId": "7", "CampaignName": "K7", "Clicks": "100", "Cost": "100.00"},
    ])
    _fake_metrika(monkeypatch)

    async def _stat(*a, **k):
        return _metrika_payload([("7", "K7", 90.0, 10.0, 0.0)])

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    assert "—" in out


async def test_403_and_empty_and_multi_account(tmp_path, monkeypatch):
    _fake_direct(monkeypatch, [
        {"CampaignId": "7", "CampaignName": "K7", "Clicks": "1", "Cost": "1.00"},
    ])
    _fake_metrika(monkeypatch)

    async def _boom(*a, **k):
        raise MetrikaError("metrika stat 11: HTTP/1.1 403 Forbidden")

    monkeypatch.setattr(_cpa, "stat_table", _boom)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    assert "Нет доступа к счётчику 11" in out

    _fake_direct(monkeypatch, [])

    async def _stat(*a, **k):
        return {"sampled": False, "data": []}

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    out = await ACTIONS["metrika_direct_cpa"].run(ctx, _params())
    assert "строк нет" in out.lower() or "Строк нет" in out

    ctx2 = Ctx(
        settings=Settings(
            auth_login="agency-login",
            accounts={
                "m": AccountEntry(alias="m", login="agency-login"),
                "m2": AccountEntry(alias="m2", login="other-login"),
            },
            accounts_path=tmp_path / "accounts.toml",
        ),
        token="t",
        data_dir=tmp_path,
    )
    out = await ACTIONS["metrika_direct_cpa"].run(ctx2, _params(account="all"))
    assert "ровно с одним кабинетом" in out


async def test_fetch_multi_merge_and_exclude(tmp_path, monkeypatch):
    from directai_mcp.catalog.metrika_cpa import _fetch_multi

    async def _stat(token, cid, *a, **k):
        if cid == 99:
            raise MetrikaError("metrika stat 99: HTTP/1.1 400 Bad Request")
        return {
            "sampled": False,
            "data": [{
                "dimensions": [{"id": "7", "name": "K7"}],
                "metrics": [90.0, 20.0, 9.0],
            }],
        }

    monkeypatch.setattr(_cpa, "stat_table", _stat)
    ctx = _ctx(tmp_path)
    payload, problems = await _fetch_multi(
        ctx, [11, 99], {11: ["5"], 99: ["5"]},
        "2026-09-11", "2026-09-24", "lastsign",
    )
    assert len(payload["data"]) == 1
    assert payload["data"][0]["metrics"] == [90.0, 20.0, 9.0]
    assert any("99" in p for p in problems)
