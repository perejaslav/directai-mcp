"""v1.1.24: group_by=weekday, fill_calendar для day, запрет Hour."""

import httpx

import directai_mcp.catalog.stats as _s  # noqa: F401 (реестр)
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings

RURL = "https://api.direct.yandex.com/json/v5/reports"

# 11.09.2026 — пятница, 12.09 — суббота, 18.09 — пятница.
DETAIL = (
    "Date\tImpressions\tClicks\tCost\n"
    "2026-09-11\t100\t5\t500.00\n"
    "2026-09-12\t50\t2\t100.00\n"
    "2026-09-18\t200\t10\t1500.00\n"
)
AGG = "Impressions\tClicks\tCost\n350\t17\t2100.00\n"


def _ctx(tmp_path):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


def _tsv(body):
    return httpx.Response(200, text=body)


async def test_weekday_buckets_order_and_days(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(return_value=_tsv(DETAIL))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_summary"].run(
        ctx, ACTIONS["stats_summary"].params(
            account="agency-login", with_conversions=False, group_by="weekday",
            date_from="2026-09-11", date_to="2026-09-18"))
    assert "| Weekday | DaysInPeriod |" in out
    assert "| Date |" not in out
    # Порядок Пн..Вс (не по расходу), суммы Decimal, дней в периоде.
    assert "| Пт | 2 | 300 | 15 |" in out
    assert "| Сб | 1 | 50 | 2 |" in out
    # Пустые дни периода — нулями (Вс..Чт без строк).
    assert "| Вс | 1 | 0 | 0 |" in out
    assert "| Пн | 1 | 0 | 0 |" in out
    order = ["| Пн |", "| Вт |", "| Ср |", "| Чт |", "| Пт |", "| Сб |", "| Вс |"]
    positions = [out.index(marker) for marker in order]
    assert positions == sorted(positions)


async def test_weekday_request_uses_date(respx_mock, tmp_path):
    import json as _json

    route = respx_mock.post(RURL).mock(return_value=_tsv(DETAIL))
    ctx = _ctx(tmp_path)
    await ACTIONS["stats_summary"].run(
        ctx, ACTIONS["stats_summary"].params(
            account="agency-login", with_conversions=False, group_by="weekday"))
    sent = _json.loads(route.calls[0].request.content)["params"]
    assert "Date" in sent["FieldNames"]
    assert "DayOfWeek" not in sent["FieldNames"]
    assert "Hour" not in sent["FieldNames"]


async def test_fill_calendar_zeros_and_note(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(return_value=_tsv(
        "Date\tImpressions\tClicks\tCost\n"
        "2026-09-11\t100\t5\t500.00\n"
        "2026-09-14\t200\t10\t1500.00\n"))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_summary"].run(
        ctx, ACTIONS["stats_summary"].params(
            account="agency-login", with_conversions=False, group_by="day",
            date_from="2026-09-11", date_to="2026-09-14"))
    assert "Нет показов: 12.09.2026, 13.09.2026." in out
    assert "| 2026-09-12 | 0 | 0 |" in out
    assert "| 2026-09-13 | 0 | 0 |" in out


async def test_fill_calendar_off(respx_mock, tmp_path):
    respx_mock.post(RURL).mock(return_value=_tsv(
        "Date\tImpressions\tClicks\tCost\n"
        "2026-09-11\t100\t5\t500.00\n"
        "2026-09-14\t200\t10\t1500.00\n"))
    ctx = _ctx(tmp_path)
    out = await ACTIONS["stats_summary"].run(
        ctx, ACTIONS["stats_summary"].params(
            account="agency-login", with_conversions=False, group_by="day",
            date_from="2026-09-11", date_to="2026-09-14",
            fill_calendar=False))
    assert "Нет показов" not in out
    assert "2026-09-12" not in out


def test_no_hour_rules_in_instructions_and_overview():
    from pathlib import Path

    from directai_mcp.server import INSTRUCTIONS

    text = " ".join(INSTRUCTIONS.split())
    assert "group_by=weekday" in text
    assert "Hour/DayOfWeek" in text
    overview = (Path(__file__).resolve().parent.parent
                / "docs" / "PUBLIC-OVERVIEW.md").read_text(encoding="utf-8")
    assert "group_by=weekday" in overview
    assert "Hour/DayOfWeek" in overview
