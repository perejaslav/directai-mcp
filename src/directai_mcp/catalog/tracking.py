"""A9: макросы трекинга + A4: итоговый множитель корректировок.

Источники списка макросов: awaik/direct-mcp-ai-project (MIT, 30.09.2026)
+ официальная справка Директа (TrackingParams / UTM-разметка).
Расхождения фиксируются в отчёте пакета «Знания», не в коде.
"""

from __future__ import annotations

import re

# Поддерживаемые макросы Трекинга (TrackingParams кампаний и групп).
# Ядро из первоисточника awaik + справка Директа.
SUPPORTED_TRACKING_MACROS = frozenset(
    {
        "{campaign_id}",
        "{campaign_name}",
        "{campaign_name_lat}",
        "{campaign_type}",
        "{adgroup_id}",
        "{adgroup_name}",
        "{ad_id}",
        "{keyword}",
        "{matchtype}",
        "{device}",
        "{position}",
        "{placement}",
        "{region_id}",
        "{region_name}",
        "{retargeting_id}",
        "{gbid}",
        "{criterion_id}",
        "{adtarget_name}",
        "{dsa_page}",
        "{source}",
        "{source_type}",
    }
)

_MACRO_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_BROKEN_RE = re.compile(r"\{[^{}]*\{|\}[^{}]*\}|\{[^{}]*$|^[^{}]*\}")


def find_macros(text: str | None) -> list[str]:
    """Все {...} в строке."""
    if not text:
        return []
    return _MACRO_RE.findall(str(text))


def validate_tracking_macros(text: str | None) -> tuple[list[str], list[str]]:
    """Проверка TrackingParams.

    Возвращает (recognized, warnings).
    - неизвестный макрос -> предупреждение с перечнем поддерживаемых;
    - явно битый синтаксис (несбалансированные скобки) -> ValueError.
    Решение зафиксировано в отчёте: блокировать только битый синтаксис.
    """
    if not text:
        return [], []
    s = str(text)
    # Грубая проверка баланса + вложенность.
    depth = 0
    for ch in s:
        if ch == "{":
            depth += 1
            if depth > 1:
                raise ValueError(f"TrackingParams: вложенные скобки: {s[:80]}.")
        elif ch == "}":
            depth -= 1
            if depth < 0:
                raise ValueError(
                    f"TrackingParams: битый синтаксис (лишняя }}): {s[:80]}."
                )
    if depth != 0:
        raise ValueError(
            f"TrackingParams: битый синтаксис (непарные скобки): {s[:80]}."
        )
    found = find_macros(s)
    recognized = [m for m in found if m in SUPPORTED_TRACKING_MACROS]
    unknown = [m for m in found if m not in SUPPORTED_TRACKING_MACROS]
    warnings: list[str] = []
    if unknown:
        warnings.append(
            "TrackingParams: неизвестные макросы "
            + ", ".join(sorted(set(unknown)))
            + ". Поддерживаются: "
            + ", ".join(sorted(SUPPORTED_TRACKING_MACROS))
            + "."
        )
    return recognized, warnings


# --- A4: итоговый множитель корректировок ---

# Категории корректировок (разные категории перемножаются,
# внутри категории побеждает наибольшая).
MODIFIER_CATEGORY = {
    "MOBILE_ADJUSTMENT": "devices",
    "TABLET_ADJUSTMENT": "devices",
    "DESKTOP_ADJUSTMENT": "devices",
    "DESKTOP_ONLY_ADJUSTMENT": "devices",
    "SMART_TV_ADJUSTMENT": "devices",
    "DEMOGRAPHICS_ADJUSTMENT": "demo",
    "INCOME_GRADE_ADJUSTMENT": "demo",
    "RETARGETING_ADJUSTMENT": "audience",
    "REGIONAL_ADJUSTMENT": "geo",
    "VIDEO_ADJUSTMENT": "format",
    "SMART_AD_ADJUSTMENT": "format",
    "SERP_LAYOUT_ADJUSTMENT": "format",
    "AD_GROUP_ADJUSTMENT": "adgroup",
}

CONVERSION_STRATEGIES = frozenset(
    {
        "AVERAGE_CPA",
        "AVERAGE_CPA_MULTIPLE_GOALS",
        "PAY_FOR_CONVERSION",
        "PAY_FOR_CONVERSION_MULTIPLE_GOALS",
        "AVERAGE_CRR",
        "PAY_FOR_CONVERSION_CRR",
        "MAX_PROFIT",
        "WB_MAXIMUM_CONVERSION_RATE",
    }
)


def calc_effective_multiplier(
    modifiers: list[dict],
    strategy_type: str | None = None,
) -> tuple[float | None, str]:
    """Итоговый множитель из списка {type, raw, level}.

    Правила (A4):
    - разные категории перемножаются;
    - внутри категории побеждает наибольшая;
    - raw=0 (-100%, показы отключены) имеет низший приоритет:
      если есть хоть одна активная корректировка категории, 0 игнорируется;
      если в категории только 0 — итог 0 (показы отключены);
    - групповая перекрывает ту же категорию кампании (передавайте уже
      отфильтрованный список: группа побеждает кампанию);
    - в конверсионных стратегиях корректировка меняет целевую CPA/ДРР,
      а не ставку — пояснение в note.

    Возвращает (multiplier | None, note).
    """
    if not modifiers:
        return None, "корректировок нет."
    by_cat: dict[str, list[int]] = {}
    for m in modifiers:
        raw = m.get("raw")
        if not isinstance(raw, int) or isinstance(raw, bool):
            continue
        cat = MODIFIER_CATEGORY.get(str(m.get("type")), str(m.get("type")))
        by_cat.setdefault(cat, []).append(raw)
    if not by_cat:
        return None, "корректировок нет."
    factors: list[str] = []
    total = 1.0
    disabled = False
    for cat, raws in sorted(by_cat.items()):
        nonzero = [r for r in raws if r != 0]
        if nonzero:
            best = max(nonzero)
        else:
            best = 0
        if best == 0:
            disabled = True
            factors.append(f"{cat}: −100% (показы отключены)")
            continue
        factors.append(f"{cat}: {best - 100:+d}%")
        total *= best / 100.0
    if disabled and len(by_cat) == 1:
        note = "; ".join(factors) + "."
    else:
        note = " × ".join(factors) + f" = {total:.3f}."
        if disabled:
            note += " Категория с −100% отключена низшим приоритетом."
    if strategy_type in CONVERSION_STRATEGIES:
        note += " Конверсионная стратегия: влияет на целевую CPA/ДРР, а не на ставку."
    if disabled and len(by_cat) == 1:
        return 0.0, note
    return round(total, 4), note
