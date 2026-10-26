#!/usr/bin/env python3
"""Проверка audit.json.

    python validate_audit.py audit.json [--json]

Код возврата: 0 — PASS, 1 — WARN, 2 — FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import validate as validate_mod  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Проверить audit.json")
    parser.add_argument("audit", type=Path)
    parser.add_argument("--json", action="store_true", help="вывести результат как JSON")
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    result = validate_mod.validate_doc(audit)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"РЕЗУЛЬТАТ: {result['status']}")
        for issue in result["issues"]:
            print(f"  [{issue['level'].upper()}] {issue['code']} {issue['message']}"
                  + (f" ({issue['path']})" if issue["path"] else ""))
        counts = result["summary"]
        print(f"ошибок: {counts['errors']}, предупреждений: {counts['warnings']}")

    return {"PASS": 0, "WARN": 1, "FAIL": 2}[result["status"]]


if __name__ == "__main__":
    sys.exit(main())
