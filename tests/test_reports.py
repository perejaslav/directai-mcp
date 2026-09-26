"""Step 2 acceptance tests for reports.py (SPEC 7.9, 9)."""

import httpx

from directai_mcp.api.reports import (
    build_report_name,
    parse_tsv,
)

URL = "https://api.direct.yandex.com/json/v5/reports"


def _queued(status: int, retry_in: str | None = "1"):
    headers = {}
    if retry_in is not None:
        headers["retryIn"] = retry_in
    return httpx.Response(status, text="", headers=headers)


def _ready(tsv: str):
    return httpx.Response(200, text=tsv)


async def test_poll_201_202_200_uses_retry_in(respx_mock):
    from directai_mcp.api.reports import ReportsClient

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    respx_mock.post(URL).mock(
        side_effect=[
            _queued(201, "3"),
            _queued(202, "4"),
            _ready("Clicks\tCost\n10\t100.50\n"),
        ]
    )
    client = ReportsClient(token="t", sleep=fake_sleep)
    try:
        columns, rows = await client.fetch("a", {"FieldNames": ["Clicks"]})
        assert columns == ["Clicks", "Cost"]
        assert rows == [{"Clicks": "10", "Cost": "100.50"}]
        assert waits == [3.0, 4.0]
    finally:
        await client.aclose()


async def test_retry_in_clamped_2_60(respx_mock):
    from directai_mcp.api.reports import ReportsClient

    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    respx_mock.post(URL).mock(
        side_effect=[
            _queued(201, "0"),
            _queued(202, "300"),
            _ready("Clicks\n1\n"),
        ]
    )
    client = ReportsClient(token="t", sleep=fake_sleep)
    try:
        await client.fetch("a", {"FieldNames": ["Clicks"]})
        assert waits == [2.0, 60.0]
    finally:
        await client.aclose()


def test_tsv_missing_and_dynamic_goal_columns():
    text = (
        "CampaignName\tClicks\tCost\tConversions_123_AUTO\tRevenue_123_AUTO\n"
        "A\t5\t100.00\t2\t500.00\n"
        "B\t--\t--\t--\t--\n"
    )
    columns, rows = parse_tsv(text)
    assert columns[3] == "Conversions_123_AUTO"
    assert rows[0]["Conversions_123_AUTO"] == "2"
    assert rows[1] == {
        "CampaignName": "B",
        "Clicks": None,
        "Cost": None,
        "Conversions_123_AUTO": None,
        "Revenue_123_AUTO": None,
    }


def test_report_name_deterministic():
    base = {"FieldNames": ["Clicks"], "ReportType": "X"}
    first = build_report_name(base)
    assert first == build_report_name(dict(base))
    assert first.startswith("dai-") and len(first) == 24
    other = build_report_name({**base, "FieldNames": ["Cost"]})
    assert other != first
    assert build_report_name({**base, "ReportName": "junk"}) == first
