"""SQLite write journal (SPEC 6.4). No tokens, no PII.

v1.13.0 («Журнал кампании», BACKLOG п.3): операции привязываются к кампаниям
в отдельной таблице operation_campaigns (схему operations не меняем);
снимки результатов — campaign_snapshots, заметки — campaign_notes.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS operations (
  id            INTEGER PRIMARY KEY,
  created_at    TEXT NOT NULL,
  plan_id       TEXT NOT NULL,
  action        TEXT NOT NULL,
  account_login TEXT NOT NULL,
  params_json   TEXT NOT NULL,
  before_json   TEXT,
  request_json  TEXT NOT NULL,
  response_json TEXT,
  after_json    TEXT,
  status        TEXT NOT NULL CHECK (status IN ('applied','partial','failed','unverified')),
  summary       TEXT NOT NULL
);
"""

# v1.13.0: привязка операций к кампаниям + снимки + заметки.
# operations не трогаем (только новые таблицы рядом).
SCHEMA_JOURNAL = """
CREATE TABLE IF NOT EXISTS operation_campaigns (
  operation_id INTEGER NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  campaign_id  INTEGER NOT NULL,
  PRIMARY KEY (operation_id, campaign_id)
);
CREATE TABLE IF NOT EXISTS campaign_snapshots (
  id            INTEGER PRIMARY KEY,
  created_at    TEXT NOT NULL,
  account_login TEXT NOT NULL,
  campaign_id   INTEGER NOT NULL,
  date_from     TEXT NOT NULL,
  date_to       TEXT NOT NULL,
  source        TEXT NOT NULL CHECK (source IN ('direct', 'metrika')),
  goal_id       TEXT,
  metrics_json  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_notes (
  id            INTEGER PRIMARY KEY,
  created_at    TEXT NOT NULL,
  account_login TEXT NOT NULL,
  campaign_id   INTEGER NOT NULL,
  kind          TEXT NOT NULL
    CHECK (kind IN ('hypothesis', 'decision', 'observation', 'todo')),
  text          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_op_campaigns_campaign
  ON operation_campaigns(campaign_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_campaign
  ON campaign_snapshots(account_login, campaign_id);
CREATE INDEX IF NOT EXISTS idx_notes_campaign
  ON campaign_notes(account_login, campaign_id);
"""

NOTE_KINDS = ("hypothesis", "decision", "observation", "todo")


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "journal.sqlite")
    conn.execute(SCHEMA)
    conn.executescript(SCHEMA_JOURNAL)
    conn.commit()
    backfill(conn)
    return conn


def _as_int(value: object) -> int | None:
    """Строгий int: bool/дроби/мусор — None (id кампаний — целые > 0)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        num = int(value.strip())
        return num if num > 0 else None
    return None


_CAMPAIGN_KEYS = frozenset({"campaign_id", "campaignid", "campaign_ids", "campaignids"})


def _collect_campaign_ids(node: object, out: set[int]) -> None:
    """Рекурсивно собрать id из campaign_id*/campaign_ids* ключей."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and key.lower() in _CAMPAIGN_KEYS:
                if isinstance(value, list):
                    for item in value:
                        num = _as_int(item)
                        if num is not None:
                            out.add(num)
                else:
                    num = _as_int(value)
                    if num is not None:
                        out.add(num)
            else:
                _collect_campaign_ids(value, out)
    elif isinstance(node, list):
        for item in node:
            _collect_campaign_ids(item, out)


def extract_campaign_ids(
    action: str,
    params: object,
    before: object,
    after: object,
) -> list[int]:
    """Привязка операции к кампаниям (v1.13.0).

    Источники: params/before/after — ключи campaign_id*/campaign_ids*
    (любой регистр). Плюс campaigns_create: id новой кампании появляется
    только в after ({id: name}). Пусто — операция без привязки (штатно,
    вызывающий пишет warning, не падает).
    """
    found: set[int] = set()
    for node in (params, before, after):
        _collect_campaign_ids(node, found)
    if action == "campaigns_create" and isinstance(after, dict):
        for key, value in after.items():
            # after read-back: {id: name} — ключи и есть id кампаний.
            if isinstance(value, str):
                num = _as_int(key)
                if num is not None:
                    found.add(num)
    return sorted(found)


def insert(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    action: str,
    account_login: str,
    params: dict,
    before: object,
    requests: list,
    response: object,
    after: object,
    status: str,
    summary: str,
) -> int:
    cur = conn.execute(
        "INSERT INTO operations (created_at, plan_id, action, account_login,"
        " params_json, before_json, request_json, response_json, after_json,"
        " status, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now().astimezone().isoformat(),
            plan_id,
            action,
            account_login,
            json.dumps(params, ensure_ascii=False),
            json.dumps(before, ensure_ascii=False) if before is not None else None,
            json.dumps(requests, ensure_ascii=False),
            json.dumps(response, ensure_ascii=False) if response is not None else None,
            json.dumps(after, ensure_ascii=False) if after is not None else None,
            status,
            summary,
        ),
    )
    operation_id = int(cur.lastrowid)
    try:
        campaign_ids = extract_campaign_ids(action, params, before, after)
    except Exception as e:  # noqa: BLE001 — привязка best-effort, не падаем
        log.warning("operation #%d: extract_campaign_ids: %s", operation_id, e)
        campaign_ids = []
    if campaign_ids:
        conn.executemany(
            "INSERT OR IGNORE INTO operation_campaigns"
            " (operation_id, campaign_id) VALUES (?, ?)",
            [(operation_id, cid) for cid in campaign_ids],
        )
    else:
        log.warning(
            "operation #%d (%s): кампания не извлечена — без привязки",
            operation_id,
            action,
        )
    conn.commit()
    return operation_id


def backfill(conn: sqlite3.Connection) -> int:
    """Одноразово привязать старые записи (идемпотентно, v1.13.0).

    Возвращает число операций, получивших привязку.
    """
    rows = conn.execute(
        "SELECT o.id, o.action, o.params_json, o.before_json, o.after_json"
        " FROM operations o"
        " WHERE NOT EXISTS (SELECT 1 FROM operation_campaigns oc"
        " WHERE oc.operation_id = o.id)"
    ).fetchall()
    bound = 0
    for op_id, action, params_json, before_json, after_json in rows:
        try:
            params = json.loads(params_json) if params_json else {}
            before = json.loads(before_json) if before_json else None
            after = json.loads(after_json) if after_json else None
            campaign_ids = extract_campaign_ids(action, params, before, after)
        except Exception as e:  # noqa: BLE001 — backfill best-effort
            log.warning("backfill op #%s: %s", op_id, e)
            continue
        if campaign_ids:
            conn.executemany(
                "INSERT OR IGNORE INTO operation_campaigns"
                " (operation_id, campaign_id) VALUES (?, ?)",
                [(op_id, cid) for cid in campaign_ids],
            )
            bound += 1
    if bound:
        conn.commit()
    return bound


def recent(
    conn: sqlite3.Connection,
    limit: int = 20,
    account_login: str | None = None,
    campaign_id: int | None = None,
) -> list[dict]:
    query = (
        "SELECT o.id, o.created_at, o.plan_id, o.action,"
        " o.account_login, o.status, o.summary FROM operations o"
    )
    args: list = []
    conds: list[str] = []
    if campaign_id is not None:
        query += " JOIN operation_campaigns oc ON oc.operation_id = o.id"
        conds.append("oc.campaign_id = ?")
        args.append(campaign_id)
    if account_login:
        conds.append("o.account_login = ?")
        args.append(account_login)
    if conds:
        query += " WHERE " + " AND ".join(conds)
    query += " ORDER BY o.id DESC LIMIT ?"
    args.append(limit)
    rows = conn.execute(query, args).fetchall()
    return [
        {
            "id": r[0],
            "created_at": r[1],
            "plan_id": r[2],
            "action": r[3],
            "account": r[4],
            "status": r[5],
            "summary": r[6],
        }
        for r in rows
    ]


def ops_for_campaign(
    conn: sqlite3.Connection,
    account_login: str,
    campaign_id: int,
    limit: int = 200,
) -> list[dict]:
    """Хронология правок кампании (по возрастанию, для журнала)."""
    rows = conn.execute(
        "SELECT o.id, o.created_at, o.plan_id, o.action,"
        " o.account_login, o.status, o.summary FROM operations o"
        " JOIN operation_campaigns oc ON oc.operation_id = o.id"
        " WHERE oc.campaign_id = ? AND o.account_login = ?"
        " ORDER BY o.id ASC LIMIT ?",
        (campaign_id, account_login, limit),
    ).fetchall()
    return [
        {
            "id": r[0],
            "created_at": r[1],
            "plan_id": r[2],
            "action": r[3],
            "account": r[4],
            "status": r[5],
            "summary": r[6],
        }
        for r in rows
    ]


def add_snapshot(
    conn: sqlite3.Connection,
    *,
    account_login: str,
    campaign_id: int,
    date_from: str,
    date_to: str,
    source: str,
    metrics: dict,
    goal_id: str | None = None,
) -> int:
    if source not in ("direct", "metrika"):
        raise ValueError(f"source must be direct|metrika, got {source!r}")
    cur = conn.execute(
        "INSERT INTO campaign_snapshots (created_at, account_login, campaign_id,"
        " date_from, date_to, source, goal_id, metrics_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            datetime.now().astimezone().isoformat(),
            account_login,
            campaign_id,
            date_from,
            date_to,
            source,
            goal_id,
            json.dumps(metrics, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_snapshots(
    conn: sqlite3.Connection, account_login: str, campaign_id: int
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, account_login, campaign_id, date_from, date_to,"
        " source, goal_id, metrics_json FROM campaign_snapshots"
        " WHERE account_login = ? AND campaign_id = ?"
        " ORDER BY date_to ASC, created_at ASC, id ASC",
        (account_login, campaign_id),
    ).fetchall()
    out = []
    for r in rows:
        try:
            metrics = json.loads(r[8]) if r[8] else {}
        except ValueError:
            metrics = {}
        out.append(
            {
                "id": r[0],
                "created_at": r[1],
                "account": r[2],
                "campaign_id": r[3],
                "date_from": r[4],
                "date_to": r[5],
                "source": r[6],
                "goal_id": r[7],
                "metrics": metrics if isinstance(metrics, dict) else {},
            }
        )
    return out


def add_note(
    conn: sqlite3.Connection,
    *,
    account_login: str,
    campaign_id: int,
    kind: str,
    text: str,
) -> int:
    if kind not in NOTE_KINDS:
        raise ValueError(f"kind must be one of {NOTE_KINDS}, got {kind!r}")
    text = str(text or "").strip()
    if not text:
        raise ValueError("text пуст: нечего сохранять.")
    cur = conn.execute(
        "INSERT INTO campaign_notes (created_at, account_login, campaign_id,"
        " kind, text) VALUES (?, ?, ?, ?, ?)",
        (
            datetime.now().astimezone().isoformat(),
            account_login,
            campaign_id,
            kind,
            text,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def list_notes(
    conn: sqlite3.Connection, account_login: str, campaign_id: int
) -> list[dict]:
    rows = conn.execute(
        "SELECT id, created_at, account_login, campaign_id, kind, text"
        " FROM campaign_notes WHERE account_login = ? AND campaign_id = ?"
        " ORDER BY id ASC",
        (account_login, campaign_id),
    ).fetchall()
    return [
        {
            "id": r[0],
            "created_at": r[1],
            "account": r[2],
            "campaign_id": r[3],
            "kind": r[4],
            "text": r[5],
        }
        for r in rows
    ]
