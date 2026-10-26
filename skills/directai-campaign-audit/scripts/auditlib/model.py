#!/usr/bin/env python3
"""Общие константы, нормализация чисел и идентификаторов (v2.0).

Никаких сетевых вызовов. Только стандартная библиотека.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timedelta
from pathlib import Path

SCHEMA_VERSION = "2.0"

MODES = ("quick", "deep", "semantic", "rsy", "funnel", "growth", "monitor", "experiment")

PRIORITIES = ("P0", "P1", "P2", "P3")
CONFIDENCES = ("high", "medium", "low")
CLASSIFICATIONS = ("fact", "likely_cause", "hypothesis")
DQ_STATES = ("passed", "warning", "blocked", "unavailable", "not_applicable")

# Состояния, которые нельзя считать успешной проверкой (ТЗ §5.5).
DQ_NOT_PASSED = ("warning", "blocked", "unavailable")

DEFAULT_THRESHOLDS = {
    "target_cpa_rub": None,          # задаёт пользователь; None — оцениваем от факта
    "target_cpl_rub": None,
    "attention_multiplier": 2.0,     # исторический ориентир 2x CPA, только внимание
    "min_spend_no_conversion_rub": 1000.0,
    "min_clicks_no_conversion": 30,
    "min_impressions": 1000,
    "min_clicks": 100,
    "min_conversions": 10,
    "alert_sensitivity": 0.20,       # порог предупреждения по относительному изменению
    "tolerance_segment_sum": 0.05,   # допустимое расхождение сегментов и итога
    "conversion_delay_days": None,   # задержка конверсий, если известна
    "protected_words": ["цена", "цены", "стоимость", "отзывы", "обзор", "купить", "заказать"],
}

PRODUCT_CONTEXT_MIN_WORDS = 1


# ---------------------------------------------------------------- значения
_MISSING_TOKENS = {"", "—", "-", "–", "n/a", "nan", "none", "null", "нет данных"}


def parse_number(value, *, unit: str | None = None):
    """Строка/число из отчёта -> float | None.

    `None` возвращается ТОЛЬКО при отсутствии значения. Ноль остаётся нулём:
    подменять пропуск нулём запрещено (ТЗ §1.2).
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    text = str(value).strip()
    if text.lower() in _MISSING_TOKENS:
        return None
    text = text.replace("\u00a0", " ").replace("\u202f", " ").replace(" ", "")
    negative = text.startswith("−") or text.startswith("-")
    text = text.lstrip("+−-")
    percent = text.endswith("%")
    text = text.rstrip("%").rstrip("п.п.").rstrip("₽").rstrip("руб.").rstrip("шт.").rstrip(".")
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return None
    if percent:
        number = number  # проценты остаются процентами, единица хранится отдельно
    return -number if negative else number


def unit_of(value) -> str | None:
    """Единица измерения, объявленная самим значением."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith("%"):
        return "percent"
    if "п.п." in text:
        return "percentage_point"
    if text.endswith("₽") or "руб" in text:
        return "RUB"
    return None


def fmt_number(value, decimals: int = 2, *, thousands: bool = True) -> str:
    """Число для человека. Округление только здесь, расчёты идут на сырых значениях."""
    if value is None:
        return "нет данных"
    if isinstance(value, bool):
        return str(value)
    if not isinstance(value, (int, float)) or (isinstance(value, float) and math.isnan(value)):
        return str(value)
    if isinstance(value, int) or float(value).is_integer() and decimals == 0:
        text = f"{int(round(value)):,}".replace(",", " ")
        return text
    text = f"{value:,.{decimals}f}"
    if thousands:
        text = text.replace(",", " ")
    return text.replace(".", ",")


def fmt_money(value, decimals: int = 2) -> str:
    """Деньги всегда с копейками: 0 и 12 000 выглядят одинаково по структуре."""
    if value is None:
        return "нет данных"
    if not isinstance(value, (int, float)):
        return str(value)
    text = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return f"{text} ₽"


def fmt_percent(value, decimals: int = 2) -> str:
    if value is None:
        return "нет данных"
    if not isinstance(value, (int, float)):
        return str(value)
    text = f"{value:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return f"{text}%"


def fmt_pp(value, decimals: int = 2) -> str:
    if value is None:
        return "нет данных"
    sign = "+" if value > 0 else ""
    return f"{sign}{fmt_number(value, decimals)} п.п."


def fmt_delta(value, decimals: int = 2, *, money: bool = False) -> str:
    if value is None:
        return "нет данных"
    sign = "+" if value > 0 else ""
    body = fmt_number(value, decimals)
    return f"{sign}{body} ₽" if money else f"{sign}{body}"


def fmt_id(value) -> str:
    """Идентификаторы рекламных объектов всегда строки (ТЗ §5.3)."""
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def as_id(value) -> str | None:
    text = fmt_id(value).strip()
    return text or None


# ---------------------------------------------------------------- даты
def iso_date(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    text = str(value).strip()[:10]
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return None
    try:
        date.fromisoformat(text)
    except ValueError:
        return None
    return text


def period_days(period: dict) -> int | None:
    start, end = iso_date(period.get("from")), iso_date(period.get("to"))
    if not start or not end:
        return None
    delta = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return delta if delta > 0 else None


def shift_period(period: dict, days: int) -> dict:
    start, end = iso_date(period["from"]), iso_date(period["to"])
    if not start or not end:
        return {}
    a = date.fromisoformat(start) - timedelta(days=days)
    b = date.fromisoformat(end) - timedelta(days=days)
    return {"from": a.isoformat(), "to": b.isoformat()}


def recent_complete_period(length_days: int, today: date | None = None) -> dict:
    """Завершённый период: вчера и раньше (сегодняшний день исключается, §6.1)."""
    today = today or date.today()
    end = today - timedelta(days=1)
    start = end - timedelta(days=length_days - 1)
    return {"from": start.isoformat(), "to": end.isoformat()}


# ---------------------------------------------------------------- объекты
def object_path(*parts) -> str:
    """Путь объекта для проверки пересечений при суммировании расходов (§8.3)."""
    return "/".join(f"{key}:{fmt_id(value)}" for key, value in parts if value is not None)


def paths_overlap(left: str, right: str) -> bool:
    """Пересечение = один путь является префиксом другого (группа ⊃ фраза ⊃ запрос)."""
    if not left or not right:
        return False
    a, b = left.strip("/"), right.strip("/")
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


# ---------------------------------------------------------------- файлы
def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def stable_id(prefix: str, *parts) -> str:
    """Детерминированный ID без хешей: читаемый и воспроизводимый."""
    body = "-".join(str(p) for p in parts if p not in (None, ""))
    body = re.sub(r"[^0-9A-Za-zА-Яа-я_.-]+", "_", body)
    return f"{prefix}-{body}".strip("-")
