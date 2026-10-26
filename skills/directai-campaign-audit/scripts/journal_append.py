#!/usr/bin/env python3
"""Журнал решений по аудиту (JSONL, локальный файл).

    python journal_append.py journal.jsonl --audit audit.json \
        --recommendation R-001 --decision accepted --by "пользователь" [--comment "..."]

Контракт: в журнал попадают только решения, принятые пользователем. Предложение
агента не может быть записано как решение. В записи нет токенов и ключей.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

DECISIONS = ("accepted", "rejected", "deferred")
SECRET_PATTERNS = [
    re.compile(r"(?i)(token|api[_-]?key|secret|password|authorization)\s*[:=]\s*\S+"),
    re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}"),
    re.compile(r"y0_[A-Za-z0-9_-]{20,}"),
]


def scrub(text: str) -> str:
    cleaned = str(text or "")
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub("[скрыто]", cleaned)
    return cleaned


def main() -> int:
    parser = argparse.ArgumentParser(description="Записать решение по рекомендации аудита")
    parser.add_argument("journal", type=Path)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--recommendation", required=True)
    parser.add_argument("--decision", required=True, choices=DECISIONS)
    parser.add_argument("--by", required=True, help="кто принял решение (пользователь)")
    parser.add_argument("--comment", default="")
    args = parser.parse_args()

    audit_bytes = args.audit.read_bytes()
    audit = json.loads(audit_bytes.decode("utf-8"))
    known = {rec["id"] for rec in audit.get("recommendations") or []}
    if args.recommendation not in known:
        print(f"ОШИБКА: рекомендации {args.recommendation} нет в {args.audit}")
        return 2
    if args.by.strip().lower() in ("агент", "agent", "ai", "assistant"):
        print("ОШИБКА: решение может записать только пользователь, не агент")
        return 2

    record = {
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "audit_id": audit.get("audit_id"),
        "audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
        "campaign_id": (audit.get("scope") or {}).get("campaign_id"),
        "recommendation_id": args.recommendation,
        "decision": args.decision,
        "decided_by": scrub(args.by),
        "comment": scrub(args.comment),
        "executed_by_agent": False,
        "note": "запись фиксирует решение; выполнение изменения в рекламе вне этого журнала",
    }
    args.journal.parent.mkdir(parents=True, exist_ok=True)
    with args.journal.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"записано: {args.journal} → {args.recommendation} = {args.decision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
