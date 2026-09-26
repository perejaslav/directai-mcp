"""SQLite write journal (SPEC 6.4). No tokens, no PII."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

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


def connect(data_dir: Path) -> sqlite3.Connection:
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / "journal.sqlite")
    conn.execute(SCHEMA)
    conn.commit()
    return conn


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
    conn.commit()
    return int(cur.lastrowid)


def recent(
    conn: sqlite3.Connection, limit: int = 20, account_login: str | None = None
) -> list[dict]:
    query = (
        "SELECT id, created_at, plan_id, action, account_login, status, summary"
        " FROM operations"
    )
    args: list = []
    if account_login:
        query += " WHERE account_login = ?"
        args.append(account_login)
    query += " ORDER BY id DESC LIMIT ?"
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
