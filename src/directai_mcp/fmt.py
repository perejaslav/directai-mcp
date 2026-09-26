"""Markdown tables, numbers, CSV/MD files (SPEC 6.6)."""

from __future__ import annotations

import json
import re
import time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

MISSING_CELL = "—"

# Hard ceiling for tool responses (DECISIONS step 2 fix 1).
MAX_TOOL_ROWS = 200

# Шаг 1.1-3: строк в сводке file-режима (смысл file — не тащить всё в контекст).
FILE_SUMMARY_ROWS = 20

SUMMABLE = ("Impressions", "Clicks", "Cost")

_NUMERIC_RE = re.compile(r"-?\d+\.\d+")


# v1.1.1: вся денежная арифметика — Decimal, ROUND_HALF_UP, без float.


def to_decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        if isinstance(value, float):
            return Decimal(str(value))
        if isinstance(value, int):
            return Decimal(value)
        text = str(value).replace(" ", "").replace(",", ".")
        return Decimal(text) if text not in ("", "—", "--") else None
    except InvalidOperation:
        return None


def to_float(value: object) -> float | None:
    """Оставлен для сортировок; деньги идут через to_decimal/money."""
    parsed = to_decimal(value)
    return float(parsed) if parsed is not None else None


def _quantize(value: Decimal, digits: int) -> Decimal:
    quantum = Decimal(1) if digits == 0 else Decimal(10) ** -digits
    return value.quantize(quantum, rounding=ROUND_HALF_UP)


def _grouped(value: Decimal, digits: int) -> str:
    text = format(_quantize(value, digits), "f")
    # v1.1.22: сортировка знака отдельно — int("-0") == 0 съедал минус
    # у значений в (-1, 0) ("-0.20" превращалось в "0.20").
    neg = text.startswith("-")
    if neg:
        text = text[1:]
    if "." in text:
        head, tail = text.split(".")
    else:
        head, tail = text, ""
    head = f"{int(head):,}".replace(",", " ")
    if neg:
        head = "-" + head
    return f"{head}.{tail}" if tail else head


def money(value: object) -> str:
    if (parsed := to_decimal(value)) is None:
        return MISSING_CELL
    return _grouped(parsed, 2)


def num(value: object, digits: int = 2) -> str:
    if (parsed := to_decimal(value)) is None:
        return MISSING_CELL
    return _grouped(parsed, digits)


def cell(value: object, money_fmt: bool = False) -> str:
    if value is None:
        return MISSING_CELL
    if money_fmt:
        return money(to_decimal(value))
    return str(value)


def _conv_total(rows: list[dict], prefix: str) -> Decimal:
    total = Decimal(0)
    for row in rows:
        for key, val in row.items():
            if key == prefix or key.startswith(prefix + "_"):
                parsed = to_decimal(val)
                if parsed is not None:
                    total += parsed
    return total


def totals(rows: list[dict]) -> dict[str, Decimal]:
    """Sum Impressions/Clicks/Cost + any Conversions_*/Revenue_* goal columns."""
    out: dict[str, Decimal] = {}
    for key in SUMMABLE:
        out[key] = sum(
            (to_decimal(r.get(key)) or Decimal(0) for r in rows), Decimal(0)
        )
    out["Conversions"] = _conv_total(rows, "Conversions")
    out["Revenue"] = _conv_total(rows, "Revenue")
    return out


GOAL_VALUE_NOTE = "условная ценность из настроек цели, не выручка"

# v1.1.9: вычисляемые колонки-проценты (знак % только в MD/inline, в CSV число).
# v1.1.19: +CR (конверсии/клики) и его суммарный вариант.
# v1.1.22: +BounceRate (доля отказов Метрики, % только в MD/inline).
PCT_COLS = ("CTR", "CostShare", "CR", "CR (сумма по целям)", "BounceRate")


def totals_line(t: dict[str, Decimal], single_goal: bool = False,
                totals_suffix: str | None = None,
                revenue_label: str | None = "Ценность целей (условная)") -> str:
    """Итоговая строка; суммы Revenue всегда условная ценность (v1.1.1–1.1.2).

    v1.1.29: revenue_label — подпись агрегата Revenue («Выручка CRM» для
    CRM-целей); None — агрегат не выводить (смешанные типы ценности).
    """
    clicks, impr, cost = t["Clicks"], t["Impressions"], t["Cost"]
    conv, revenue = t["Conversions"], t["Revenue"]
    ctr = (clicks / impr * 100) if impr else None
    cpc = (cost / clicks) if clicks else None
    cpa = (cost / conv) if conv else None
    parts = [
        f"Показы: {num(impr, 0)}",
        f"Клики: {num(clicks, 0)}",
        f"CTR: {num(ctr)}%",
        f"Расход: {money(cost)} ₽",
        f"CPC: {money(cpc)} ₽",
    ]
    if conv:
        parts += [f"Конверсии: {num(conv, 0)}", f"CPA: {money(cpa)} ₽"]
        # v1.1.26: CR итога — только из сумм (не усреднение по строкам).
        if clicks:
            parts += [f"CR: {num(conv / clicks * 100)}%"]
    if revenue and not single_goal and revenue_label:
        # v1.1.26: «Выручки» нет — только условная ценность.
        parts += [f"{revenue_label}: {money(revenue)} ₽"]
    line = "Итого: " + "; ".join(parts) + "."
    if totals_suffix:
        line += f" {totals_suffix}"
    return line


def top_totals_line(n: int, t: dict[str, Decimal],
                    total_cost: Decimal | None = None) -> str:
    """v1.1.9: «Итого топ-N» по показанным строкам + доля от итога отчёта."""
    parts = [
        f"Показы: {num(t['Impressions'], 0)}",
        f"Клики: {num(t['Clicks'], 0)}",
        f"Расход: {money(t['Cost'])} ₽",
    ]
    if total_cost:
        parts.append(f"Доля: {num(t['Cost'] / total_cost * 100)}%")
    if t["Conversions"]:
        parts.append(f"Конверсии: {num(t['Conversions'], 0)}")
    return f"Итого топ-{n}: " + "; ".join(parts) + "."


def render_table(
    context: str,
    columns: list[str],
    rows: list[dict],
    limit: int,
    money_cols: tuple[str, ...] = ("Cost", "AvgCpc", "Revenue"),
    with_totals: bool = True,
    single_goal: bool = False,
    totals_override: dict | None = None,
    totals_suffix: str | None = None,
    top_line: str | None = None,
    pct_cols: tuple[str, ...] = PCT_COLS,
    # v1.1.29: подпись агрегата Revenue (None — не выводить).
    revenue_label: str | None = "Ценность целей (условная)",
    # v1.1.26: переименования заголовков только для MD/inline
    # (CSV/JSON хранят имена полей API).
    header_map: dict[str, str] | None = None,
    # v1.1.27: подписи значений ячеек только для MD/inline
    # (CSV/JSON хранят сырые значения API).
    value_map: dict[str, dict[str, str]] | None = None,
) -> str:
    """Markdown table capped at `limit` rows, sorted by Cost desc upstream."""
    shown = rows[:limit]
    hidden = len(rows) - len(shown)
    lines = [context, ""]
    if not shown and not rows:
        lines.append("Строк нет.")
        return "\n".join(lines)
    head = [(header_map or {}).get(c, c) for c in columns]
    lines.append("| " + " | ".join(head) + " |")
    lines.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in shown:
        cells = []
        for c in columns:
            value = row.get(c)
            text = cell(value, c in money_cols)
            if text != MISSING_CELL and c in pct_cols:
                text += "%"
            # v1.1.27: человеческие подписи сырых значений (Slot и т.п.).
            mapping = (value_map or {}).get(c)
            if mapping and value is not None and str(value) in mapping:
                text = mapping[str(value)]
            cells.append(text)
        lines.append("| " + " | ".join(cells) + " |")
    if hidden > 0:
        lines.append("")
        lines.append(f"Скрыто строк: {hidden} из {len(rows)}.")
    if with_totals and rows:
        if top_line:
            lines.append("")
            lines.append(top_line)
        lines.append("")
        base = totals_override if totals_override is not None else totals(rows)
        lines.append(totals_line(base, single_goal, totals_suffix, revenue_label))
    return "\n".join(lines)


def _csv_cell(value: object) -> str:
    """CSV cell: empty for missing, comma as decimal separator (ru Excel)."""
    if value is None:
        return ""
    text = str(value)
    if _NUMERIC_RE.fullmatch(text):
        return text.replace(".", ",")
    return text


def save_csv(
    exports_dir: Path, base: str, columns: list[str], rows: list[dict]
) -> Path:
    """Full result to UTF-8 BOM CSV with ';' (opens correctly in Excel)."""
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = exports_dir / f"{base}-{stamp}.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        f.write(";".join(columns) + "\n")
        for row in rows:
            f.write(";".join(_csv_cell(row.get(c)) for c in columns) + "\n")
    return path


def save_md(
    exports_dir: Path, base: str, context: str, columns: list[str], rows: list[dict],
    single_goal: bool = False,
    totals_override: dict | None = None,
    totals_suffix: str | None = None,
    top_line: str | None = None,
) -> Path:
    """Full result as Markdown table with header (account, dates, totals)."""
    exports_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = exports_dir / f"{base}-{stamp}.md"
    body = render_table(context, columns, rows, max(len(rows), 1),
                        single_goal=single_goal, totals_override=totals_override,
                        totals_suffix=totals_suffix, top_line=top_line)
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(f"# {base}\n\n{body}\n")
    return path


def _safe_account(value: str | None) -> str:
    text = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_-]+", "_", value or "all").strip("_")
    return text or "all"


def report_filename(action: str, account: str | None, ext: str) -> str:
    """Шаг 1.1-3: <action>_<account|all>_<YYYYMMDD-HHMMSS>.<ext>."""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{action}_{_safe_account(account)}_{stamp}.{ext}"


def save_text(reports_dir: Path, action: str, account: str | None, body: str) -> Path:
    """v1.1.30: произвольный MD-текст в каталог отчётов (counter_check)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / report_filename(action, account, "md")
    path.write_text(body, encoding="utf-8")
    return path


def save_json(
    reports_dir: Path,
    action: str,
    account: str | None,
    context: str,
    columns: list[str],
    rows: list[dict],
) -> Path:
    """Шаг 1.1-3: полные строки (все поля нормализованного вывода) + мета."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / report_filename(action, account, "json")
    payload = {
        "meta": {
            "action": action,
            "account": account or "all",
            "context": context,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "total_rows": len(rows),
            "columns": columns,
        },
        "rows": rows,
    }
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1, default=str)
    return path


def save_report_table(
    reports_dir: Path,
    action: str,
    account: str | None,
    context: str,
    columns: list[str],
    rows: list[dict],
    ext: str,
    single_goal: bool = False,
    totals_override: dict | None = None,
    totals_suffix: str | None = None,
    top_line: str | None = None,
    header_map: dict[str, str] | None = None,
    value_map: dict[str, dict[str, str]] | None = None,
    # v1.1.29: подпись агрегата Revenue (None — не выводить).
    revenue_label: str | None = "Ценность целей (условная)",
) -> Path:
    """Шаг 1.1-3: md/csv в каталог отчётов с именем по шаблону шага 3."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / report_filename(action, account, ext)
    if ext == "md":
        body = render_table(context, columns, rows, max(len(rows), 1),
                            single_goal=single_goal,
                            totals_override=totals_override,
                            totals_suffix=totals_suffix,
                            top_line=top_line, header_map=header_map,
                            value_map=value_map,
                            revenue_label=revenue_label)
        with path.open("w", encoding="utf-8", newline="") as f:
            f.write(f"# {action}\n\n{body}\n")
    else:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            f.write(";".join(columns) + "\n")
            for row in rows:
                f.write(";".join(_csv_cell(row.get(c)) for c in columns) + "\n")
    return path


def truncated_line(total: int, shown: int) -> str:
    """Шаг 1.1-3: явный флаг обрезки в каждом inline-ответе."""
    flag = "true" if total > shown else "false"
    line = f"truncated: {flag}, показано {shown} из {total} строк."
    if total > shown:
        line += " Полный результат: повторите с output=file."
    return line
