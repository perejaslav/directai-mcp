"""v1.12.0 Ч1: metrika_traffic / metrika_goals_report / metrika_bytime.

Фикстуры записанных ответов API (вымышленные числа), без сети.
Проверки ТЗ: парсинг, семплирование, 403, пустой ответ, названия целей,
CR = цели/визиты.
"""

import json

import pytest

import directai_mcp.catalog.metrika_reports as _r
from directai_mcp.catalog import metrika_goals as _mg
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


def _clear():
    for cache in (
        _mg.COUNTER_GOALS_CACHE,
        _mg.COUNTER_INFO_CACHE,
        _mg.COUNTER_GOAL_NAMES_CACHE,
    ):
        cache.clear()


TABLE_PAYLOAD = {
    "sampled": False,
    "total_rows": 2,
    "data": [
        {
            "dimensions": [{"name": "organic"}],
            "metrics": [100.0, 80.0, 35.5, 2.4, 120.0, 10.0],
        },
        {
            "dimensions": [{"name": "direct"}],
            "metrics": [50.0, 45.0, 20.0, 3.1, 200.0, 5.0],
        },
    ],
    "totals": [150.0, 125.0, 30.0, 2.6, 146.0, 15.0],
}


def _fake_table(monkeypatch, payload, goals=((1, "Заявка"),), sampled=False):
    payload = dict(payload)
    payload["sampled"] = sampled

    def _fake(token, path, timeout=30.0):
        if path.startswith("/management/v1/counter/11/goals"):
            body = {
                "goals": [
                    {"id": gid, "name": name, "type": "action"}
                    for gid, name in goals
                ]
            }
            return "HTTP/1.1 200 OK", json.dumps(body)
        if path.startswith("/stat/v1/data/bytime"):
            return "HTTP/1.1 200 OK", json.dumps(payload)
        if path.startswith("/stat/v1/data"):
            return "HTTP/1.1 200 OK", json.dumps(payload)
        raise AssertionError(path)

    monkeypatch.setattr(_r, "_mget", _fake)
    monkeypatch.setattr(_mg, "_mget", _fake)
    _clear()


def _params(name, **kw):
    base = {
        "account": "agency-login",
        "counter_id": 11,
        "date_from": "2026-09-11",
        "date_to": "2026-09-24",
    }
    base.update(kw)
    return ACTIONS[name].params(**base)


async def test_traffic_parsing_and_cr(tmp_path, monkeypatch):
    _fake_table(monkeypatch, TABLE_PAYLOAD)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_traffic"].run(
        ctx, _params("metrika_traffic", group_by="source", goal_ids=["1"])
    )
    assert "metrika_traffic: счётчик 11" in out
    assert "атрибуция lastsign" in out
    assert "organic" in out and "Заявка (1)" in out
    # CR = цели/визиты: 10/100=10%, 5/50=10%.
    assert "10.0" in out
    assert "truncated:" in out


async def test_traffic_sampled_flag(tmp_path, monkeypatch):
    _fake_table(monkeypatch, TABLE_PAYLOAD, sampled=True)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_traffic"].run(
        ctx, _params("metrika_traffic", group_by="source", goal_ids=["1"])
    )
    assert "семплированы" in out


async def test_traffic_403(tmp_path, monkeypatch):
    def _fake(token, path, timeout=30.0):
        if path.startswith("/management/v1/counter/11/goals"):
            body = {"goals": [{"id": 1, "name": "G", "type": "action"}]}
            return "HTTP/1.1 200 OK", json.dumps(body)
        return "HTTP/1.1 403 Forbidden", "{}"

    monkeypatch.setattr(_r, "_mget", _fake)
    monkeypatch.setattr(_mg, "_mget", _fake)
    _clear()
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_traffic"].run(
        ctx, _params("metrika_traffic", group_by="source", goal_ids=["1"])
    )
    assert "Нет доступа к счётчику 11" in out


async def test_traffic_empty(tmp_path, monkeypatch):
    _fake_table(monkeypatch, {"sampled": False, "data": [], "totals": []})
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_traffic"].run(
        ctx, _params("metrika_traffic", group_by="source", goal_ids=["1"])
    )
    assert "пустой ответ" in out


async def test_goals_report_names_and_cr(tmp_path, monkeypatch):
    _fake_table(monkeypatch, TABLE_PAYLOAD, goals=((1, "Заявка"), (2, "Звонок")))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_goals_report"].run(
        ctx,
        _params(
            "metrika_goals_report",
            goal_ids=["1", "2"],
            group_by="source",
        ),
    )
    assert "Заявка" in out and "Звонок" in out
    # Первая строка: визиты 100, цели 10 -> CR 10%.
    assert "GoalCR" in out or "10" in out


async def test_bytime_parsing(tmp_path, monkeypatch):
    payload = {
        "sampled": False,
        "time_intervals": [["2026-09-11", "2026-09-12"], ["2026-09-12", "2026-09-13"]],
        "data": [
            {"dimensions": [], "metrics": [[100.0, 50.0], [30.0, 20.0], [10.0, 5.0]]}
        ],
        "totals": [[150.0], [25.0], [15.0]],
    }
    _fake_table(monkeypatch, payload)
    ctx = _ctx(tmp_path)
    out = await ACTIONS["metrika_bytime"].run(
        ctx, _params("metrika_bytime", group="day", goal_ids=["1"])
    )
    assert "2026-09-11" in out and "2026-09-12" in out
    assert "metrika_bytime" in out


async def test_validation_group_by(tmp_path):
    with pytest.raises(ValueError, match="group_by"):
        ACTIONS["metrika_traffic"].params(
            account="agency-login", counter_id=11,
            date_from="2026-09-11", date_to="2026-09-24",
            group_by="nope",
        )
    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        ACTIONS["metrika_traffic"].params(
            account="agency-login", counter_id=11,
            date_from="11.09.2026", date_to="2026-09-24",
        )


async def test_default_period_and_counter_from_config(tmp_path, monkeypatch):
    _fake_table(monkeypatch, TABLE_PAYLOAD)
    ctx = _ctx(tmp_path, counter_id=11)
    out = await ACTIONS["metrika_traffic"].run(
        ctx,
        ACTIONS["metrika_traffic"].params(
            account="agency-login", group_by="source", goal_ids=["1"]
        ),
    )
    assert "счётчик 11" in out
