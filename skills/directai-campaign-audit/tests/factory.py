#!/usr/bin/env python3
"""Фабрика синтетических сессий аудита для тестов.

Все данные вымышлены. Формат повторяет реальные ответы DirectAI MCP:
конверт {meta, rows}, числа строками с пробелами, «—» вместо отсутствия.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _num(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}".replace(",", " ")
    return f"{int(value):,}".replace(",", " ")


def _pct(value) -> str:
    return f"{value:.2f}"


def _row(**kwargs) -> dict:
    return {k: v for k, v in kwargs.items() if v is not None}


def envelope(action: str, rows: list, *, account: str = "demo-agency", context: str = "",
             columns: list | None = None) -> dict:
    return {
        "meta": {
            "action": action,
            "account": account,
            "context": context,
            "created_at": "2026-10-20T10:00:00+0300",
            "total_rows": len(rows),
            "columns": columns or (list(rows[0].keys()) if rows else []),
        },
        "rows": rows,
    }


def stats_context(*, period_a, period_b=None, goal=None, attribution="AUTO",
                  goal_source="config", note="") -> str:
    parts = []
    if period_b:
        parts.append(f"A: {period_a['from']}, B: {period_b['from']}, расход с НДС, атрибуция: {attribution}")
    else:
        parts.append(f"{period_a['from']}–{period_a['to']}, расход с НДС, атрибуция: {attribution}")
    parts.append(f"Атрибуция: requested={attribution}, effective={attribution}, source={goal_source}")
    if goal:
        parts.append(f"Цель: id={goal['id']}, label={goal['label']}, source={goal_source}.")
    else:
        parts.append("Цель: id=—, label=—, source=none. Внимание: основная цель не задана, CPA по всем целям.")
    if note:
        parts.append(note)
    return ". ".join(parts)


def metric_block(impressions, clicks, cost, conversions):
    data = {
        "impressions": impressions,
        "clicks": clicks,
        "cost": cost,
        "conversions": conversions,
    }
    data["ctr"] = round(clicks / impressions * 100, 2) if impressions and clicks is not None else None
    data["cpc"] = round(cost / clicks, 2) if clicks and cost is not None else None
    data["cr"] = round(conversions / clicks * 100, 2) if clicks and conversions is not None else None
    data["cpa"] = round(cost / conversions, 2) if conversions and cost is not None else None
    return data


def compare_rows(a: dict, b: dict) -> list:
    """Повторяет поведение stats_compare: Δ% от базы A и перевёрнутый знак у части метрик."""
    def row(name, va, vb, *, kind="number"):
        if va is None or vb is None:
            return {"Метрика": name, "A": "—", "B": "—", "Δ": "—", "Δ%": "—"}
        if kind == "rate":
            delta = f"+{va - vb:.2f} п.п." if va >= vb else f"-{abs(va - vb):.2f} п.п."
        else:
            delta = _num(va - vb)
        pct = f"{(vb - va) / va * 100:+.2f}%" if va else "—"
        return {"Метрика": name, "A": _num(va) if kind != "rate" else f"{va:.2f}",
                "B": _num(vb) if kind != "rate" else f"{vb:.2f}",
                "Δ": delta, "Δ%": pct}

    rows = [
        row("Показы", a.get("impressions"), b.get("impressions")),
        row("Клики", a.get("clicks"), b.get("clicks")),
        row("CTR, %", a.get("ctr"), b.get("ctr"), kind="rate"),
        row("Расход, ₽", a.get("cost"), b.get("cost")),
        row("CPC, ₽", a.get("cpc"), b.get("cpc")),
        row("Конверсии (достижения)", a.get("conversions"), b.get("conversions")),
        row("CR, %", a.get("cr"), b.get("cr"), kind="rate"),
        row("CPA, ₽", a.get("cpa"), b.get("cpa")),
    ]
    return rows


def segment_row(key: str, metrics: dict, key_name: str = "Ключ") -> dict:
    return _row(**{
        key_name: key,
        "Impressions": _num(metrics.get("impressions")),
        "Clicks": _num(metrics.get("clicks")),
        "CTR": _pct(metrics["ctr"]) if metrics.get("ctr") is not None else None,
        "Cost": f"{metrics['cost']:.2f}" if metrics.get("cost") is not None else None,
        "Conversions": _num(metrics.get("conversions")),
        "CPA": f"{metrics['cpa']:.2f}" if metrics.get("cpa") is not None else None,
        "CR": _pct(metrics["cr"]) if metrics.get("cr") is not None else None,
    })


def make_session(root: Path, *, campaign_id="900000001", campaign_name="Демо-кампания",
                 account="demo", login="demo-agency",
                 modes=("deep",), a=None, b=None, campaign_type="UNIFIED_CAMPAIGN",
                 strategy="поиск: WB_MAXIMUM_CLICKS; сети: SERVING_OFF",
                 state="ON", start_date="2026-01-15", counters=("12345678",),
                 goal=("11111111", "Заявка с сайта"), attribution="AUTO",
                 product=None, thresholds=None, economics=None,
                 queries=(), placements=(), devices=(), regions=(), keywords=(),
                 adgroups=(), ads=(), moderation=(), negatives=(),
                 metrika=None, changes=(), journal=None, previous=None,
                 sources=None, periods=None, extra_sources=()) -> Path:
    """Создаёт папку сессии. `sources=None` — объявить все доступные источники."""
    root = Path(root)
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    a = a or dict(impressions=100000, clicks=2000, cost=120000, conversions=40)
    b = b or dict(impressions=95000, clicks=1950, cost=118000, conversions=45)
    ma, mb = metric_block(**a), metric_block(**b)
    periods = periods or {"a": {"from": "2026-09-26", "to": "2026-10-09"},
                          "b": {"from": "2026-09-12", "to": "2026-09-25"}}
    context = stats_context(period_a=periods["a"], period_b=periods["b"],
                            goal={"id": goal[0], "label": goal[1]} if goal else None,
                            attribution=attribution)

    files = {}
    files["stats_compare.json"] = envelope("stats_compare", compare_rows(ma, mb), context=context)
    files["campaigns_get.json"] = envelope("campaigns_get", [_row(**{
        "Id": int(campaign_id), "Name": campaign_name, "Type": campaign_type,
        "State": state, "Status": "ACCEPTED", "StartDate": start_date, "EndDate": None,
        "TimeZone": "Europe/Moscow", "DailyBudget": "—", "StatusPayment": "ALLOWED",
        "Strategy": strategy, "Attrib": attribution,
        "Counters": ", ".join(counters) if counters else "—",
        "Negatives": "0: —", "_account": login,
    })], context=f"campaigns_get: {login}.")
    files["stats_adgroups.json"] = envelope("stats_adgroups", [
        segment_row(name, metric_block(**m), "AdGroupName") for name, m in adgroups
    ], context=context)
    files["stats_keywords.json"] = envelope("stats_keywords", [
        segment_row(text, metric_block(**m), "Keyword") for text, m in keywords
    ], context=context)
    files["stats_search_queries.json"] = envelope("stats_search_queries", [
        segment_row(text, metric_block(**m), "Query") for text, m in queries
    ], context=context)
    files["stats_placements.json"] = envelope("stats_placements", [
        segment_row(name, metric_block(**m), "Placement") for name, m in placements
    ], context=context)
    files["stats_devices.json"] = envelope("stats_devices", [
        segment_row(name, metric_block(**m), "Device") for name, m in devices
    ], context=context)
    files["stats_regions.json"] = envelope("stats_regions", [
        segment_row(name, metric_block(**m), "Region") for name, m in regions
    ], context=context)
    files["stats_ads.json"] = envelope("stats_ads", [], context=context)

    ads_rows = []
    for ad_id, gaps in ads:
        row = {"Id": str(ad_id), "AdGroupId": "555", "State": "ON", "Status": "ACCEPTED"}
        for name in ("DisplayUrlPath", "Sitelinks", "Callouts", "VCardId", "Images"):
            row[name] = "—" if name in gaps else "заполнено"
        ads_rows.append(row)
    files["ads_list.json"] = envelope("ads_list", ads_rows, context=f"ads_list: {login}.")

    files["moderation_check.json"] = envelope("moderation_check", [
        _row(**{"Id": str(ad_id), "Status": status, "RejectReason": reason, "_account": login})
        for ad_id, status, reason in moderation
    ], context=f"moderation_check: {login}.")

    neg_rows = []
    for level, ident, values in negatives:
        neg_rows.append({"Level": level, "Id": str(ident), "Negatives": ", ".join(values)})
    files["negatives_audit.json"] = envelope("negatives_audit", neg_rows, context=f"negatives_audit: {login}.")

    keyword_rows = [{"Id": f"k{index}", "Keyword": text, "AdGroupId": "555", "State": "ON"}
                    for index, (text, _m) in enumerate(keywords, start=1)]
    files["keywords_list.json"] = envelope("keywords_list", keyword_rows, context=f"keywords_list: {login}.")

    if metrika is not None:
        files["metrika_direct_cpa.json"] = envelope("metrika_direct_cpa", [_row(**{
            "Campaign": f"Демо-кампания ({campaign_id})",
            "Cost": metrika.get("cost", ma["cost"]),
            "Clicks": metrika.get("clicks", ma["clicks"]),
            "Visits": metrika.get("visits"),
            "ClicksToVisits": f"{metrika['visits'] / metrika['clicks'] * 100:.1f}%"
                              if metrika.get("visits") and metrika.get("clicks") else "—",
            "BounceRate": metrika.get("bounce_rate", 22.0),
            "Goals": metrika.get("goals", ma["conversions"]),
            "CR": metrika.get("cr"),
            "CPA": metrika.get("cpa"),
            "Flag": "—", "Связь": "ID",
        })], context=f"metrika_direct_cpa: {login}, атрибуция lastsign.")

    files["changes_check.json"] = envelope("changes_check", [
        {"Статус": "Изменён", "Тип": kind, "Id": str(ident), "BorderDate": when}
        for kind, ident, when in changes
    ], context=f"changes_check: {login}.")

    declared = []
    for name, payload in files.items():
        # В реальных ответах meta.account — тот кабинет, по которому читали.
        payload["meta"]["account"] = login
        (raw / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        declared.append({"file": name, "action": payload["meta"]["action"],
                         "params": {"campaign_ids": [int(campaign_id)]},
                         "status": "ok", "pagination_complete": True, "truncated": False})

    if journal is not None:
        (raw / "campaign_journal.md").write_text(journal, encoding="utf-8")
        declared.append({"file": "campaign_journal.md", "action": "campaign_journal", "status": "ok"})

    for name, payload in extra_sources:
        (raw / name).write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        declared.append({"file": name, "action": payload["meta"]["action"], "status": "ok"})

    if sources is not None:
        keep = set(sources)
        declared = [item for item in declared if item["action"] in keep or item["file"] in keep]

    session = {
        "schema_version": "2.0",
        "account": account,
        "account_login": login,
        "campaign_id": str(campaign_id),
        "modes": list(modes),
        "periods": periods,
        "goal": {"id": goal[0], "label": goal[1], "source": "campaign"} if goal else {},
        "attribution": {"requested": attribution, "effective": attribution, "source": "config"},
        "thresholds": thresholds or {},
        "product": product if product is not None else {
            "what": "окна ПВХ под ключ", "exclude_terms": ["вакансии", "работа"],
            "protected_terms": ["цена", "отзывы"]},
        "economics": economics or {},
        "sources": declared,
        "notes": [],
    }
    if previous:
        session["previous_audit"] = str(previous)
    (root / "session.json").write_text(json.dumps(session, ensure_ascii=False, indent=2), encoding="utf-8")
    return root
