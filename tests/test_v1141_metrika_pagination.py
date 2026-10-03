"""v1.14.1: stat/v1/data — все страницы (offset), честный truncated."""

import json
from urllib.parse import parse_qs, urlparse

from directai_mcp.catalog import metrika_goals as _mg
from directai_mcp.catalog import metrika_reports as _r


def _rows(start, count):
    return [
        {"dimensions": [{"id": str(i), "name": f"c{i}"}],
         "metrics": [float(i), 1.0]}
        for i in range(start, start + count)
    ]


def _fake_paged(monkeypatch, total, calls):
    def _fake(token, path, timeout=30.0):
        q = parse_qs(urlparse(path).query)
        calls.append(q)
        offset = int(q["offset"][0])
        limit = int(q["limit"][0])
        count = max(0, min(limit, total - offset + 1))
        body = {"data": _rows(offset, count), "total_rows": total,
                "sampled": False}
        return "HTTP/1.1 200 OK", json.dumps(body)

    monkeypatch.setattr(_r, "_mget", _fake)
    monkeypatch.setattr(_mg, "_mget", _fake)


async def test_stat_table_reads_all_pages(monkeypatch):
    calls: list = []
    _fake_paged(monkeypatch, total=250, calls=calls)
    out = await _r.stat_table(
        "t", 1, "2026-07-05", "2026-10-02", ["ym:s:lastDirectClickOrder"],
        ["ym:s:visits", "ym:s:goal1reaches"], "lastsign", None, limit=100,
    )
    assert len(out["data"]) == 250
    assert out["truncated"] is False
    assert [c["offset"][0] for c in calls] == ["1", "101", "201"]


async def test_stat_table_truncated_over_cap(monkeypatch):
    calls: list = []
    _fake_paged(monkeypatch, total=500, calls=calls)
    out = await _r.stat_table(
        "t", 1, "2026-07-05", "2026-10-02", ["ym:s:lastDirectClickOrder"],
        ["ym:s:visits"], "lastsign", None, limit=100, max_rows=200,
    )
    assert len(out["data"]) == 200
    assert out["truncated"] is True
    assert _r.truncation_note(out)


async def test_stat_table_no_dimensions_single_request(monkeypatch):
    calls: list = []
    _fake_paged(monkeypatch, total=1, calls=calls)
    out = await _r.stat_table(
        "t", 1, "2026-07-05", "2026-10-02", [], ["ym:s:visits"],
        "lastsign", None,
    )
    assert len(calls) == 1
    assert out["truncated"] is False


async def test_stat_table_chunked_metrics_all_pages(monkeypatch):
    """>20 метрик: чанки по целям, каждый — со всеми страницами."""
    calls: list = []
    _fake_paged(monkeypatch, total=150, calls=calls)
    metrics = ["ym:s:visits"] + [f"ym:s:goal{i}reaches" for i in range(25)]
    out = await _r.stat_table(
        "t", 1, "2026-07-05", "2026-10-02", ["ym:s:lastDirectClickOrder"],
        metrics, "lastsign", None, limit=100,
    )
    assert len(out["data"]) == 150
    assert out["truncated"] is False
