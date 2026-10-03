"""v1.13.0 «Журнал кампании» (BACKLOG п.3).

extract_campaign_ids по всем write-действиям, backfill, сборка md-файла,
фильтр get_operation_log(campaign_id), будущие даты в snapshot до API.
Без сети (API мокается там, где вызывается).
"""

import datetime as _dt
import sqlite3

import pytest

import directai_mcp.catalog.campaign_journal as cj
import directai_mcp.server as server_mod
from directai_mcp.catalog.registry import ACTIONS, Ctx
from directai_mcp.config import AccountEntry, Settings
from directai_mcp.safety import journal as journal_mod
from directai_mcp.safety.journal import extract_campaign_ids


def _ctx(tmp_path, **kw):
    settings = Settings(
        auth_login="agency-login",
        accounts={"m": AccountEntry(alias="m", login="agency-login")},
        accounts_path=tmp_path / "accounts.toml",
        reports_dir=tmp_path / "reports",
        **kw,
    )
    return Ctx(settings=settings, token="t", data_dir=tmp_path)


# action -> (params, before, after, expected). [] — штатно без привязки.
CASES = [
    ("campaigns_state", {"campaign_ids": [11, 12]}, None, None, [11, 12]),
    ("campaigns_create", {"name": "X"}, None, {"11": "X"}, [11]),
    ("campaigns_update", {"campaign_ids": [7]}, {"7": "N"}, {"7": "date"}, [7]),
    ("adgroups_create", {"campaign_id": 5, "groups": []}, None, None, [5]),
    (
        "adgroups_update",
        {"groups": [{"id": 9}]},
        {"names": {"9": "g"}, "campaign_ids": [5]},
        None,
        [5],
    ),
    ("ads_create", {"adgroup_id": 9}, {"campaign_id": 5, "adgroup_id": 9}, None, [5]),
    ("ads_update", {"ad_ids": [3]}, None, None, []),
    # v1.14.1: prepare кладёт CampaignId объявления в before.
    ("ads_update", {"ad_ids": [3]}, {"3": {"campaign_id": 5}}, None, [5]),
    ("ads_state", {"ad_ids": [3], "operation": "suspend"}, None, None, []),
    ("keywords_add", {"adgroup_id": 9}, {"campaign_id": 5, "adgroup_id": 9}, None, [5]),
    ("keywords_update", {"keywords": [{"id": 4}]}, None, None, []),
    ("keywords_state", {"keyword_ids": [4]}, None, None, []),
    (
        "negatives_set",
        {"campaign_ids": [7], "negatives": ["-x"]},
        {"campaign_ids": [7], "adgroup_campaigns": {}},
        None,
        [7],
    ),
    (
        "negatives_set",
        {"adgroup_ids": [9], "negatives": ["-x"]},
        {"campaign_ids": [5], "adgroup_campaigns": {"9": 5}},
        None,
        [5],
    ),
    ("extensions_create", {"callouts": ["скидки"]}, None, None, []),
    ("bids_set", {"campaign_ids": [7], "search_bid": 10.0}, None, None, [7]),
    (
        "bids_set",
        {"keyword_ids": [4]},
        {"bids": {"4": {}}, "campaign_ids": [5]},
        None,
        [5],
    ),
    ("bid_modifiers_set", {"add_items": [{"campaign_id": 7}]}, None, None, [7]),
    (
        "bid_modifiers_set",
        {"set_items": [{"id": 1}]},
        {"1": {"value": 100, "campaign_id": 8}},
        None,
        [8],
    ),
    ("retargeting_list_create", {"name": "rl"}, None, None, []),
    ("retargeting_list_update", {"list_id": 1}, None, None, []),
    ("retargeting_list_delete", {"list_id": 1}, None, None, []),
    (
        "audience_target_add",
        {"adgroup_id": 9},
        {"campaign_id": 5, "adgroup_id": 9},
        None,
        [5],
    ),
    ("audience_target_state", {"target_ids": [2]}, None, None, []),
    ("audience_segment_from_file", {"name": "s"}, None, None, []),
    ("audience_segment_delete", {"segment_id": 3}, None, None, []),
]

WRITE_ACTIONS = {
    "campaigns_create",
    "campaigns_update",
    "campaigns_state",
    "adgroups_create",
    "adgroups_update",
    "ads_create",
    "ads_update",
    "ads_state",
    "keywords_add",
    "keywords_update",
    "keywords_state",
    "negatives_set",
    "extensions_create",
    "bids_set",
    "bid_modifiers_set",
    "retargeting_list_create",
    "retargeting_list_update",
    "retargeting_list_delete",
    "audience_target_add",
    "audience_target_state",
    "audience_segment_from_file",
    "audience_segment_delete",
}


def test_write_actions_covered():
    registered = {n for n, a in ACTIONS.items() if a.mode == "write"}
    assert WRITE_ACTIONS <= registered
    covered = {c[0] for c in CASES}
    assert WRITE_ACTIONS <= covered


@pytest.mark.parametrize("action,params,before,after,expected", CASES)
def test_extract_campaign_ids(action, params, before, after, expected):
    assert extract_campaign_ids(action, params, before, after) == expected


def test_extract_ignores_non_campaign_ints():
    # id фраз/групп/объявлений без campaign-ключей — не кампании.
    assert (
        extract_campaign_ids(
            "keywords_state", {"keyword_ids": [4, 5]}, {"4": "ON"}, None
        )
        == []
    )


def _insert_op(conn, action="negatives_set", params=None, **kw):
    return journal_mod.insert(
        conn,
        plan_id="p1",
        action=action,
        account_login="agency-login",
        params=params or {"campaign_ids": [7]},
        before=kw.get("before", {"campaign_ids": [7], "adgroup_campaigns": {}}),
        requests=[],
        response=None,
        after=kw.get("after"),
        status="applied",
        summary="ok",
    )


def test_insert_binds_and_recent_filters(tmp_path):
    conn = journal_mod.connect(tmp_path)
    _insert_op(conn, params={"campaign_ids": [7]})
    _insert_op(
        conn, action="extensions_create", params={"callouts": ["x"]}, before=None
    )
    assert [r["id"] for r in journal_mod.recent(conn, 10, None, 7)] == [1]
    assert journal_mod.recent(conn, 10, None, 999) == []
    assert len(journal_mod.recent(conn, 10)) == 2
    conn.close()


def test_backfill_idempotent(tmp_path):
    path = tmp_path / "journal.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(journal_mod.SCHEMA)
    conn.execute(
        "INSERT INTO operations (created_at, plan_id, action, account_login,"
        " params_json, before_json, request_json, response_json, after_json,"
        " status, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "2026-10-02T10:00:00+03:00",
            "p1",
            "negatives_set",
            "agency-login",
            '{"campaign_ids": [7]}',
            None,
            "[]",
            None,
            None,
            "applied",
            "ok",
        ),
    )
    conn.commit()
    conn.close()
    conn = journal_mod.connect(tmp_path)  # backfill внутри connect
    assert journal_mod.recent(conn, 10, None, 7)[0]["action"] == "negatives_set"
    first = conn.execute("SELECT COUNT(*) FROM operation_campaigns").fetchone()[0]
    conn.close()
    conn = journal_mod.connect(tmp_path)  # повтор — ничего не дублирует
    second = conn.execute("SELECT COUNT(*) FROM operation_campaigns").fetchone()[0]
    conn.close()
    assert (first, second) == (1, 1)


def _header(name="Test"):
    return {
        "name": name,
        "type": "UNIFIED_CAMPAIGN",
        "strategy": "Search=X",
        "state": "ON",
        "warning": "",
    }


def _goal():
    return {"id": "1", "label": "g", "source": "account"}


def test_render_empty_campaign():
    text = cj.build_journal_markdown(
        login="agency-login",
        campaign_id=7,
        header=_header(),
        goal=_goal(),
        ops=[],
        snapshots=[],
        notes=[],
        built_at="2026-10-02T12:00:00+03:00",
    )
    assert "## Результаты по периодам" in text
    assert "## История правок" in text
    assert "## Заметки" in text
    assert "## Правки ↔ результаты" in text
    assert text.count("нет данных") == 4


def test_render_edits_without_snapshots_marks_unverified():
    ops = [
        {
            "id": 1,
            "created_at": "2026-10-02T10:00:00+03:00",
            "plan_id": "p",
            "action": "negatives_set",
            "account": "agency-login",
            "status": "unverified",
            "summary": "минусы добавлены",
        },
    ]
    text = cj.build_journal_markdown(
        login="agency-login",
        campaign_id=7,
        header=_header(),
        goal=_goal(),
        ops=ops,
        snapshots=[],
        notes=[],
        built_at="2026-10-02T12:00:00+03:00",
    )
    assert "negatives_set" in text
    assert "⚠" in text  # unverified помечен
    assert "нет данных" in text  # снимки/notes/линковка пусты


def test_render_snapshots_with_delta():
    snaps = [
        {
            "id": 1,
            "created_at": "2026-09-20T10:00:00+03:00",
            "account": "agency-login",
            "campaign_id": 7,
            "date_from": "2026-09-10",
            "date_to": "2026-09-16",
            "source": "direct",
            "goal_id": "1",
            "metrics": {"cost": 1000.0, "clicks": 100, "conversions": 10},
        },
        {
            "id": 2,
            "created_at": "2026-09-20T10:05:00+03:00",
            "account": "agency-login",
            "campaign_id": 7,
            "date_from": "2026-09-10",
            "date_to": "2026-09-16",
            "source": "metrika",
            "goal_id": "1",
            "metrics": {"visits": 90, "bounce_rate": 20.0, "goals": 10, "cpa": 100.0},
        },
        {
            "id": 3,
            "created_at": "2026-09-27T10:00:00+03:00",
            "account": "agency-login",
            "campaign_id": 7,
            "date_from": "2026-09-17",
            "date_to": "2026-09-23",
            "source": "direct",
            "goal_id": "1",
            "metrics": {"cost": 1500.0, "clicks": 120, "conversions": 12},
        },
        {
            "id": 4,
            "created_at": "2026-09-27T10:05:00+03:00",
            "account": "agency-login",
            "campaign_id": 7,
            "date_from": "2026-09-17",
            "date_to": "2026-09-23",
            "source": "metrika",
            "goal_id": "1",
            "metrics": {"visits": 110, "bounce_rate": 18.0, "goals": 12, "cpa": 125.0},
        },
    ]
    ops = [
        {
            "id": 5,
            "created_at": "2026-09-24T10:00:00+03:00",
            "plan_id": "p",
            "action": "bids_set",
            "account": "agency-login",
            "status": "applied",
            "summary": "ставки",
        },
    ]
    notes = [
        {
            "id": 1,
            "created_at": "2026-09-24T11:00:00+03:00",
            "account": "agency-login",
            "campaign_id": 7,
            "kind": "todo",
            "text": "проверить ставки",
        },
        {
            "id": 2,
            "created_at": "2026-09-24T11:05:00+03:00",
            "account": "agency-login",
            "campaign_id": 7,
            "kind": "decision",
            "text": "подняли ставки",
        },
    ]
    text = cj.build_journal_markdown(
        login="agency-login",
        campaign_id=7,
        header=_header(),
        goal=_goal(),
        ops=ops,
        snapshots=snaps,
        notes=notes,
        built_at="2026-10-02T12:00:00+03:00",
    )
    assert "+500.00" in text  # Δ расхода A−B
    assert text.index("[todo]") < text.index("[decision]")  # todo сверху
    # Линковка правки к снимкам до/после.
    assert "до — #2 metrika 2026-09-10–2026-09-16" in text
    assert "после — #3 direct 2026-09-17–2026-09-23" in text
    assert "Только факты рядом" in text


def test_get_operation_log_campaign_filter(tmp_path):
    conn = journal_mod.connect(tmp_path)
    _insert_op(conn, params={"campaign_ids": [7]})
    _insert_op(conn, action="campaigns_update", params={"campaign_ids": [8]}, before={})
    conn.close()
    ctx = _ctx(tmp_path)
    out = server_mod.do_get_log(ctx, 20, None, 7)
    assert "negatives_set" in out and "campaigns_update" not in out
    assert server_mod.do_get_log(ctx, 20, None, 999) == "Журнал пуст."


async def test_snapshot_future_rejected_before_api(tmp_path, monkeypatch):
    from directai_mcp.api.direct import DirectClient
    from directai_mcp.api.reports import ReportsClient

    async def _boom(*args, **kwargs):
        raise AssertionError("API не должен вызываться")

    monkeypatch.setattr(ReportsClient, "fetch", _boom)
    monkeypatch.setattr(DirectClient, "get_all", _boom)
    today = _dt.datetime.now().astimezone().date()
    future_from = (today + _dt.timedelta(days=5)).isoformat()
    future_to = (today + _dt.timedelta(days=10)).isoformat()
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaign_journal_snapshot"].run(
        ctx,
        ACTIONS["campaign_journal_snapshot"].params(
            account="agency-login",
            campaign_id=7,
            date_from=future_from,
            date_to=future_to,
        ),
    )
    assert out.startswith("Ошибка: даты в будущем")


def test_note_kind_validation(tmp_path):
    conn = journal_mod.connect(tmp_path)
    with pytest.raises(ValueError):
        journal_mod.add_note(
            conn, account_login="a", campaign_id=7, kind="spam", text="x"
        )
    with pytest.raises(ValueError):
        journal_mod.add_note(
            conn, account_login="a", campaign_id=7, kind="todo", text="  "
        )
    nid = journal_mod.add_note(
        conn, account_login="a", campaign_id=7, kind="todo", text="проверить"
    )
    assert nid == 1
    assert journal_mod.list_notes(conn, "a", 7)[0]["kind"] == "todo"
    conn.close()


async def test_journal_writes_file(tmp_path, monkeypatch):
    async def _fake_header(ctx, login, campaign_id):
        return {
            "name": "Поиск",
            "type": "UNIFIED_CAMPAIGN",
            "strategy": "Search=X",
            "state": "ON",
            "warning": "",
        }

    monkeypatch.setattr(cj, "_campaign_header", _fake_header)
    conn = journal_mod.connect(tmp_path)
    _insert_op(conn, params={"campaign_ids": [7]})
    journal_mod.add_snapshot(
        conn,
        account_login="agency-login",
        campaign_id=7,
        date_from="2026-09-17",
        date_to="2026-09-23",
        source="direct",
        metrics={"cost": 100.0, "clicks": 10, "conversions": 1},
        goal_id="1",
    )
    journal_mod.add_note(
        conn,
        account_login="agency-login",
        campaign_id=7,
        kind="decision",
        text="тестовая заметка",
    )
    conn.close()
    ctx = _ctx(tmp_path)
    out = await ACTIONS["campaign_journal"].run(
        ctx, ACTIONS["campaign_journal"].params(account="agency-login", campaign_id=7)
    )
    path = tmp_path / "reports" / "journals" / "agency-login" / "7.md"
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "negatives_set" in body and "тестовая заметка" in body
    assert f"Файл: {path}." in out
