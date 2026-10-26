#!/usr/bin/env python3
"""Сравнение показателей и проверка статистической надёжности (ТЗ §4.4, §6.4, §6.5).

Правила:
- A — свежий период, B — предыдущий.
- Δ = A − B, Δ% = (A − B) / B × 100.
- Для процентных метрик дополнительно процентные пункты: pp = A − B.
- B = 0 → относительного изменения нет («нет базы для сравнения»), не бесконечность.
- Округление для отображения не влияет на расчёты, но фиксируется флагом.
- Доверительные интервалы и p-value считаются только когда данных достаточно.
"""
from __future__ import annotations

import math

from .model import fmt_number

KINDS = ("count", "money", "rate", "per_click")


def safe_div(numerator, denominator):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def compare(a, b, *, kind: str = "count", decimals: int = 2) -> dict:
    """Сравнение двух значений одного показателя.

    Возвращает и сырые числа, и представление, и флаг «изменение скрыто округлением».
    """
    result = {
        "a": a,
        "b": b,
        "kind": kind,
        "available": a is not None and b is not None,
        "abs": None,
        "pct": None,
        "pp": None,
        "base_zero": False,
        "display_hides_change": False,
        "note": None,
    }
    if a is None or b is None:
        missing = [name for name, value in (("A", a), ("B", b)) if value is None]
        result["note"] = f"нет значения: {', '.join(missing)}"
        return result

    result["abs"] = a - b
    if kind == "rate":
        result["pp"] = a - b
    if b == 0:
        result["base_zero"] = True
        result["note"] = "нет базы для сравнения: B = 0"
        return result
    result["pct"] = (a - b) / b * 100

    same_display = fmt_number(a, decimals) == fmt_number(b, decimals)
    if result["abs"] != 0 and same_display:
        result["display_hides_change"] = True
        result["note"] = (
            f"изменение {fmt_number(result['abs'], 4)} не видно при округлении "
            f"до {decimals} знаков"
        )
    return result


def ctr(clicks, impressions):
    return safe_div(clicks, impressions) * 100 if safe_div(clicks, impressions) is not None else None


def cpc(cost, clicks):
    return safe_div(cost, clicks)


def cr(conversions, clicks):
    return safe_div(conversions, clicks) * 100 if safe_div(conversions, clicks) is not None else None


def cpa(cost, conversions):
    """CPA отсутствует при нуле конверсий: бесконечность не подставляется (§6.5)."""
    return safe_div(cost, conversions)


# --------------------------------------------------------- значимость
def _norm_sf(z: float) -> float:
    """P(Z > z) для стандартного нормального распределения через erfc."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_proportion_z(k1: int, n1: int, k2: int, n2: int) -> dict:
    """Сравнение двух долей (CTR, CR). Возвращает z и двусторонний p.

    `valid=False`, если нормальное приближение неприменимо — тогда вывод
    «недостаточно статистики», а не выдуманный p-value.
    """
    out = {"valid": False, "z": None, "p": None, "reason": None}
    if min(n1, n2) <= 0:
        out["reason"] = "нулевая база наблюдений"
        return out
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    if min(n1 * pooled, n1 * (1 - pooled), n2 * pooled, n2 * (1 - pooled)) < 5:
        out["reason"] = "мало наблюдений для нормального приближения (np или n(1-p) < 5)"
        return out
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        out["reason"] = "нулевая дисперсия"
        return out
    z = (p1 - p2) / se
    out.update({"valid": True, "z": z, "p": 2 * _norm_sf(abs(z))})
    return out


def poisson_ratio_ci(k1: int, k2: int, *, alpha: float = 0.05) -> dict:
    """Отношение двух частот (конверсии) — интервал по методу Каца, лог-шкала.

    Применим при k1 + k2 > 0. Это приближение, поэтому в отчёт идёт как
    «оценка», а не как точный p-value.
    """
    out = {"valid": False, "ratio": None, "ci_low": None, "ci_high": None}
    if k1 < 0 or k2 < 0 or (k1 + k2) == 0:
        return out
    # z для 95% по умолчанию; для других alpha — приближение через обратную функцию erfc.
    z = _z_from_alpha(alpha)
    if k1 == 0:
        low, high = 0.0, (z * z) / max(k2, 1)
    elif k2 == 0:
        low, high = max(k1, 1) / (z * z), math.inf
    else:
        ratio = k1 / k2
        se = math.sqrt(1 / k1 + 1 / k2)
        low = math.exp(math.log(ratio) - z * se)
        high = math.exp(math.log(ratio) + z * se)
    out.update({
        "valid": True,
        "ratio": (k1 / k2) if k2 else None,
        "ci_low": low,
        "ci_high": high,
        "approximation": "Katz log-normal",
    })
    return out


def _z_from_alpha(alpha: float) -> float:
    """Обратная функция нормального распределения (бисекция, без scipy)."""
    lo, hi = 0.0, 10.0
    target = 1 - alpha / 2
    for _ in range(80):
        mid = (lo + hi) / 2
        if 1 - _norm_sf(mid) < target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def sufficiency(impressions=None, clicks=None, conversions=None, thresholds=None) -> dict:
    """Достаточно ли данных для вывода. Возвращает статус и чего не хватает."""
    th = thresholds or {}
    min_impressions = th.get("min_impressions", 1000)
    min_clicks = th.get("min_clicks", 100)
    min_conversions = th.get("min_conversions", 10)

    gaps = []
    if impressions is not None and impressions < min_impressions:
        gaps.append(f"показов {impressions} < {min_impressions}")
    if clicks is not None and clicks < min_clicks:
        gaps.append(f"кликов {clicks} < {min_clicks}")
    if conversions is not None and conversions < min_conversions:
        gaps.append(f"конверсий {conversions} < {min_conversions}")
    return {
        "sufficient": not gaps,
        "gaps": gaps,
        "thresholds": {
            "min_impressions": min_impressions,
            "min_clicks": min_clicks,
            "min_conversions": min_conversions,
        },
    }


def adjust_alpha(alpha: float, tests: int) -> dict:
    """Поправка на множественные сравнения: порог делится на число проверок."""
    tests = max(int(tests), 1)
    return {"tests": tests, "alpha": alpha, "alpha_adjusted": alpha / tests,
            "method": "Bonferroni"}


def is_significant(p, alpha=0.05) -> bool:
    return p is not None and p < alpha


def trend_word(delta_pct, alert=20.0):
    """Словесная оценка динамики. Не заменяет статистическую проверку."""
    if delta_pct is None:
        return "нет базы для сравнения"
    if abs(delta_pct) < alert:
        return "стабильно"
    return "рост" if delta_pct > 0 else "падение"
