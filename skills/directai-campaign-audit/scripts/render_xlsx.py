#!/usr/bin/env python3
"""Excel-отчёт из audit.json.

    python render_xlsx.py audit.json [out.xlsx] [--diagnostic]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import render_xlsx  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Excel-отчёт из audit.json")
    parser.add_argument("audit", type=Path)
    parser.add_argument("out", nargs="?", type=Path, default=None)
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()

    audit = json.loads(args.audit.read_text(encoding="utf-8"))
    status = (audit.get("validation") or {}).get("status", "FAIL")
    if status == "FAIL" and not args.diagnostic:
        print("ОТКАЗ: валидация вернула FAIL. Публикация запрещена; используйте --diagnostic.")
        return 2

    data = render_xlsx.render(audit)
    out = args.out or args.audit.with_suffix(".xlsx")
    out.write_bytes(data)
    print(f"excel: {out} ({len(data)} байт)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
