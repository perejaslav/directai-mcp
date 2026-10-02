"""v1.12.1 хотфикс: counter_check отклоняет будущие даты до API.

Direct Reports отвечает 4001 («DateFrom не позднее текущей даты»),
поэтому валидация — upfront, без сети (вымышленные даты).
"""

import datetime as _dt

import directai_mcp.catalog.counters as _c  # noqa: F401 (реестр)
from directai_mcp.catalog.counters import _check_dates_not_future
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def test_past_dates_ok():
    assert _check_dates_not_future("2026-09-18", "2026-10-01") is None


def test_future_dates_rejected():
    today = _dt.datetime.now().astimezone().date()
    future_from = (today + _dt.timedelta(days=5)).isoformat()
    future_to = (today + _dt.timedelta(days=10)).isoformat()
    err = _check_dates_not_future(future_from, future_to)
    assert err is not None and "4001" in err and "будущем" in err
    # Только date_to в будущем — тоже отклоняем.
    err2 = _check_dates_not_future("2026-09-18", future_to)
    assert err2 is not None and "4001" in err2


async def test_check_rejects_future_before_api(tmp_path, monkeypatch):
    from directai_mcp.api.reports import ReportsClient

    async def _boom(self, login, definition):
        raise AssertionError("API не должен вызываться")

    monkeypatch.setattr(ReportsClient, "fetch", _boom)
    today = _dt.datetime.now().astimezone().date()
    future_from = (today + _dt.timedelta(days=5)).isoformat()
    future_to = (today + _dt.timedelta(days=10)).isoformat()
    ctx = _ctx(tmp_path)
    out = await ACTIONS["counter_check"].run(
        ctx,
        ACTIONS["counter_check"].params(
            account="agency-login", campaign_ids=[7],
            date_from=future_from, date_to=future_to))
    assert out.startswith("Ошибка: даты в будущем")
