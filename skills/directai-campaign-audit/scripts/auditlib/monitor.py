#!/usr/bin/env python3
"""Совместимость двух снимков аудита перед сравнением (ТЗ §8.5, §9 monitor).

Сравнивать можно только снимки одной кампании одного кабинета с одной целью
и одной моделью атрибуции. Иначе динамика будет описывать разные объекты.
"""
from __future__ import annotations


def _value(container: dict, *path, default=None):
    node = container or {}
    for key in path:
        if not isinstance(node, dict):
            return default
        node = node.get(key)
    return default if node in (None, "") else node


def compatibility(previous: dict, current: dict) -> dict:
    """Сверяет кабинет, кампанию, цель и атрибуцию двух снимков.

    Все четыре поля обязательны. Если цель или атрибуция не указаны хотя бы
    в одном снимке, CPA, CR и конверсии сравнивать нельзя: неизвестно, что
    именно и по какой модели считается.
    """
    prev_account = _value(previous, "scope", "account_login") or _value(previous, "scope", "account")
    curr_account = _value(current, "scope", "account_login") or _value(current, "scope", "account")
    prev_campaign = _value(previous, "scope", "campaign_id")
    curr_campaign = _value(current, "scope", "campaign_id")
    prev_goal = _value(previous, "goal", "id")
    curr_goal = _value(current, "goal", "id")
    prev_attr = _value(previous, "attribution", "effective") or _value(previous, "attribution", "requested")
    curr_attr = _value(current, "attribution", "effective") or _value(current, "attribution", "requested")

    checks = []

    def add(field: str, label: str, left, right, *, required: bool):
        if left is None and right is None:
            status = "unknown"
        elif left is None or right is None:
            status = "unknown"
        elif str(left) == str(right):
            status = "match"
        else:
            status = "mismatch"
        checks.append({
            "field": field,
            "label": label,
            "previous": left,
            "current": right,
            "status": status,
            "required": required,
        })

    add("account", "Кабинет", prev_account, curr_account, required=True)
    add("campaign_id", "Кампания", prev_campaign, curr_campaign, required=True)
    add("goal_id", "Основная цель", prev_goal, curr_goal, required=True)
    add("attribution", "Модель атрибуции", prev_attr, curr_attr, required=True)

    mismatches = [c for c in checks if c["status"] == "mismatch"]
    unknowns = [c for c in checks if c["status"] == "unknown"]
    required_unknown = [c for c in unknowns if c["required"]]
    compatible = not mismatches and not required_unknown
    conversion_comparable = all(
        check["status"] == "match"
        for check in checks if check["field"] in ("goal_id", "attribution")
    )

    reasons = []
    for check in mismatches:
        reasons.append(f"{check['label']}: было {check['previous']}, стало {check['current']}")
    for check in required_unknown:
        reasons.append(f"{check['label']}: не указан в одном из снимков — "
                       "сравнение конверсионных показателей запрещено")
    for check in unknowns:
        if not check["required"]:
            reasons.append(f"{check['label']}: не указан, сравнение этого поля невозможно")

    return {
        "compatible": compatible,
        "conversion_comparable": conversion_comparable,
        "checks": checks,
        "mismatches": mismatches,
        "unknowns": unknowns,
        "reasons": reasons,
        "summary": "; ".join(reasons) if reasons else "кабинет, кампания, цель и атрибуция совпадают",
    }
