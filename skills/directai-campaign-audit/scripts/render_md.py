#!/usr/bin/env python3
"""Markdown-отчёт из audit.json.

    python render_md.py audit.json [out.md] [--diagnostic]

При статусе FAIL публикация запрещена: отчёт создаётся только с --diagnostic
и помечается как диагностический.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import render_md  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown-отчёт из audit.json")
    parser.add_argument("audit", type=Path)
    parser.add_argument("out", nargs="?", type=Path, default=None)
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    status = (audit.get("validation") or {}).get("status", "FAIL")
    if status == "FAIL" and not args.diagnostic:
        print("ОТКАЗ: валидация вернула FAIL. Публикация как подтверждённого отчёта запрещена.\n"
              "Для диагностического отчёта повторите с флагом --diagnostic.")
        return 2

    text = render_md.render(audit)
    out = args.out or args.audit.with_suffix(".md")
    out.write_text(text, encoding="utf-8")
    print(f"markdown: {out} ({len(text)} символов)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
