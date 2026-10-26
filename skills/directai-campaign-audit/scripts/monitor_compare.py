#!/usr/bin/env python3
"""Сравнение двух аудитов (режим monitor).

    python monitor_compare.py previous.json current.json [--json]

Сравнение строится только на реально существующих снимках audit.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from auditlib import metrics as met  # noqa: E402
from auditlib import monitor as monitor_mod  # noqa: E402
from auditlib.model import fmt_money, fmt_number, fmt_percent  # noqa: E402

KEYS = ("impressions", "clicks", "cost", "conversions", "cpa", "ctr", "cr")


def compare(previous: dict, current: dict) -> dict:
    compatibility = monitor_mod.compatibility(previous, current)
    base = {
        "previous_audit_id": previous.get("audit_id"),
        "current_audit_id": current.get("audit_id"),
        "previous_created_at": previous.get("created_at"),
        "current_created_at": current.get("created_at"),
        "compatibility": compatibility,
        "comparable": compatibility["compatible"],
    }
    if not compatibility["compatible"]:
        return {**base, "metric_deltas": {}, "new_rules": [], "resolved_rules": [],
                "persisting_rules": [], "recommendations_added": [],
                "recommendations_removed": [],
                "data_quality": {
                    "previous": (previous.get("data_quality") or {}).get("status"),
                    "current": (current.get("data_quality") or {}).get("status"),
                }}

    prev_a = (previous.get("metrics") or {}).get("a") or {}
    curr_a = (current.get("metrics") or {}).get("a") or {}
    deltas = {}
    for key in KEYS:
        kind = "rate" if key in ("ctr", "cr") else ("money" if key in ("cost", "cpa") else "count")
        deltas[key] = met.compare(curr_a.get(key), prev_a.get(key), kind=kind)

    prev_findings = {f.get("rule_id") for f in previous.get("findings") or []}
    curr_findings = {f.get("rule_id") for f in current.get("findings") or []}
    prev_recs = {r.get("id") for r in previous.get("recommendations") or []}
    curr_recs = {r.get("id") for r in current.get("recommendations") or []}

    return {
        **base,
        "metric_deltas": deltas,
        "new_rules": sorted(curr_findings - prev_findings),
        "resolved_rules": sorted(prev_findings - curr_findings),
        "persisting_rules": sorted(curr_findings & prev_findings),
        "recommendations_added": sorted(curr_recs - prev_recs),
        "recommendations_removed": sorted(prev_recs - curr_recs),
        "data_quality": {
            "previous": (previous.get("data_quality") or {}).get("status"),
            "current": (current.get("data_quality") or {}).get("status"),
        },
    }


def as_markdown(report: dict) -> str:
    lines = [f"# Сравнение аудитов", "",
             f"- Было: `{report['previous_audit_id']}` ({report['previous_created_at']})",
             f"- Стало: `{report['current_audit_id']}` ({report['current_created_at']})", "",
             "| Метрика | Было | Стало | Δ | Δ% |", "|---|---|---|---|---|"]
    for key, item in report["metric_deltas"].items():
        a, b = item.get("a"), item.get("b")
        if key in ("cost", "cpa"):
            fa, fb = fmt_money(a), fmt_money(b)
        elif key in ("ctr", "cr"):
            fa, fb = fmt_percent(a), fmt_percent(b)
        else:
            fa, fb = fmt_number(a, 0), fmt_number(b, 0)
        pct = "нет базы" if item.get("base_zero") else (
            fmt_percent(item.get("pct")) if item.get("pct") is not None else "—")
        lines.append(f"| {key} | {fa} | {fb} | {fmt_number(item.get('abs'), 2)} | {pct} |")
    lines += ["", f"Новые правила: {', '.join(report['new_rules']) or 'нет'}",
              f"Закрытые правила: {', '.join(report['resolved_rules']) or 'нет'}",
              f"Сохраняются: {', '.join(report['persisting_rules']) or 'нет'}"]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Сравнить два audit.json")
    parser.add_argument("previous", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    previous = json.loads(args.previous.read_text(encoding="utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    report = compare(previous, current)
    if not report["comparable"]:
        print("ОТКАЗ: снимки несопоставимы — сравнение не выполнено.")
        for reason in report["compatibility"]["reasons"]:
            print(f"  - {reason}")
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else as_markdown(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
