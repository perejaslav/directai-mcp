#!/usr/bin/env python3
"""Автономный HTML-отчёт из audit.json.

    python render_html.py audit.json [out.html] [--diagnostic]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import render_html  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="HTML-отчёт из audit.json")
    parser.add_argument("audit", type=Path)
    parser.add_argument("out", nargs="?", type=Path, default=None)
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    status = (audit.get("validation") or {}).get("status", "FAIL")
    if status == "FAIL" and not args.diagnostic:
        print("ОТКАЗ: валидация вернула FAIL. Публикация запрещена; используйте --diagnostic.")
        return 2

    text = render_html.render(audit)
    out = args.out or args.audit.with_suffix(".html")
    out.write_text(text, encoding="utf-8")
    print(f"html: {out} ({len(text)} символов, внешних зависимостей нет)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
