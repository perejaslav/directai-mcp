#!/usr/bin/env python3
"""Сборка audit.json из папки сессии чтения.

    python build_audit.py <session_dir> [--out audit.json] [--previous prev.json]

Папка сессии: session.json + raw/*.json (ответы run_read с output=file).
Скрипт не вызывает MCP и не меняет рекламу: только читает файлы.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import build as build_mod  # noqa: E402
from auditlib.rawio import SessionError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Собрать audit.json из сессии чтения")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--out", "-o", type=Path, default=None)
    parser.add_argument("--previous", type=Path, default=None,
                        help="предыдущий audit.json для режима monitor")
    parser.add_argument("--today", type=str, default=None,
                        help="дата аудита YYYY-MM-DD (для воспроизводимых тестов)")
    args = parser.parse_args()

    today = date.fromisoformat(args.today) if args.today else None
    try:
        audit = build_mod.build(args.session_dir, previous_audit=args.previous, today=today)
    except SessionError as exc:
        print(f"ОШИБКА СЕССИИ: {exc}")
        return 2

    out = args.out or (args.session_dir / "audit.json")
    build_mod.write(audit, out)
    validation = audit["validation"]
    print(f"audit.json: {out}")
    print(f"валидация: {validation['status']} "
          f"(ошибок {validation['summary']['errors']}, предупреждений {validation['summary']['warnings']})")
    print(f"находок: {len(audit['findings'])}, рекомендаций: {len(audit['recommendations'])}, "
          f"гипотез: {len(audit['hypotheses'])}")
    for issue in validation["issues"]:
        if issue["level"] == "error":
            print(f"  FAIL {issue['code']}: {issue['message']}")
    return 0 if validation["status"] != "FAIL" else 1


if __name__ == "__main__":
    sys.exit(main())
