#!/usr/bin/env python3
"""Правила аудита: находки, рекомендации, гипотезы (ТЗ §6–§10).

Каждое правило возвращает структуры с доказательствами. Ни одна рекомендация
не создаётся без проверки данных и без способа проверки результата.
"""
from __future__ import annotations

from datetime import date, datetime

from . import metrics as met
from .model import fmt_id, fmt_money, object_path, parse_number, stable_id
from .negatives import (classify_intent, conflicts_with_keywords, fully_overlaps_keyword,
                        negative_blocks, suggest_negatives)

MODE_SCOPES = {
    "quick": {"core"},
    "deep": {"core", "semantic", "rsy", "funnel", "growth", "history"},
    "semantic": {"core", "semantic"},
    "rsy": {"core", "rsy"},
    "funnel": {"core", "funnel"},
    "growth": {"core", "growth"},
    "monitor": {"core", "monitor"},
    "experiment": {"core", "semantic", "experiment"},
}


def scopes_for(modes: list[str]) -> set[str]:
    scopes = set()
    for mode in modes or ["deep"]:
        scopes |= MODE_SCOPES.get(mode, set())
    return scopes or {"core"}


class Engine:
    def __init__(self, ctx: dict):
        self.ctx = ctx
        self.session = ctx["session"]
        self.th = ctx["thresholds"]
        self.a = ctx["metrics"]["a"]
        self.b = ctx["metrics"]["b"]
        self.cmp = ctx["metrics"]["comparison"]
        self.scopes = scopes_for(self.session.get("modes") or ["deep"])
        self.gates = ctx["dq"].get("gates", {})
        self.findings: list[dict] = []
        self.recommendations: list[dict] = []
        self.hypotheses: list[dict] = []
        self.growth: list[dict] = []
        self.limitations: list[dict] = list(ctx.get("limitations") or [])
        self._f = 0
        self._r = 0
        self._h = 0

    # ------------------------------------------------------------- утилиты
    def enabled(self, scope: str) -> bool:
        return scope in self.scopes

    def blocked(self, group: str) -> bool:
        return bool(self.gates.get(group))

    def limit(self, text: str, *, group: str | None = None, detail: str = ""):
        item = {"id": f"L-{len(self.limitations) + 1:02d}", "text": text, "detail": detail}
        if group:
            item["group"] = group
        if not any(existing["text"] == text for existing in self.limitations):
            self.limitations.append(item)

    def finding(self, *, rule: str, category: str, title: str, obj: dict, evidence: list,
                metrics: dict, classification: str = "fact", priority: str = "P2",
                confidence: str = "high", threshold: dict | None = None,
                why: str = "", alternatives: list | None = None, next_step: str = "",
                spend: dict | None = None) -> dict:
        self._f += 1
        finding = {
            "id": f"F-{self._f:03d}",
            "rule_id": rule,
            "category": category,
            "title": title,
            "object": obj,
            "evidence": evidence,
            "metrics": metrics,
            "threshold": threshold,
            "classification": classification,
            "priority": priority,
            "confidence": confidence,
            "why": why,
            "alternatives": alternatives or [],
            "next_step": next_step,
            "spend": spend or {"actual": None, "at_risk": None, "potential_saving": None},
            "recommendation_id": None,
        }
        self.findings.append(finding)
        return finding

    def recommend(self, finding: dict, *, problem: str, change: str, effect: dict,
                  risk: dict, apply: dict, verify: dict, priority: str | None = None,
                  confidence: str | None = None) -> dict:
        self._r += 1
        rec = {
            "id": f"R-{self._r:03d}",
            "finding_id": finding["id"],
            "rule_id": finding["rule_id"],
            "priority": priority or finding["priority"],
            "confidence": confidence or finding["confidence"],
            "problem": problem,
            "object": finding["object"],
            "evidence": finding["evidence"],
            "proposed_change": change,
            "expected_effect": effect,
            "risk": risk,
            "apply": apply,
            "verify": verify,
            "status": "proposed",
        }
        finding["recommendation_id"] = rec["id"]
        self.recommendations.append(rec)
        return rec

    def hypothesis(self, *, title: str, statement: str, basis: list, alternatives: list,
                   check: str, priority: str = "P3", source_finding: str | None = None) -> dict:
        self._h += 1
        item = {
            "id": f"H-{self._h:02d}",
            "title": title,
            "statement": statement,
            "classification": "hypothesis",
            "basis": basis,
            "alternatives": alternatives,
            "check": check,
            "priority": priority,
            "source_finding": source_finding,
        }
        self.hypotheses.append(item)
        return item

    # ------------------------------------------------------- вспомогательное
    def verify_template(self, metrics_list: list[str], *, direction: str,
                        side_effects: list[str] | None = None,
                        success: str | None = None, failure: str | None = None) -> dict:
        return {
            "baseline_period": self.ctx["periods"]["a"],
            "control_metrics": metrics_list,
            "expected_direction": direction,
            "sufficiency": {
                "min_impressions": self.th.get("min_impressions"),
                "min_clicks": self.th.get("min_clicks"),
                "min_conversions": self.th.get("min_conversions"),
                "note": "вывод делать только при наборе объёма; иначе — наблюдать",
            },
            "side_effects": side_effects or [],
            "success": success or "целевой показатель улучшился при сопоставимом объёме трафика",
            "failure": failure or "изменение в пределах колебаний или ухудшение — откатить",
        }

    def period_cost(self):
        return self.a.get("cost")

    def waste_priority(self, at_risk: float | None, confidence: str) -> str:
        """Приоритет не определяется только суммой (§10.1)."""
        if confidence == "low":
            return "P3"
        if at_risk is None or not self.period_cost():
            return "P2"
        share = at_risk / self.period_cost()
        if confidence == "high" and (share >= 0.05 or at_risk >= (self.th.get("min_spend_no_conversion_rub") or 1000) * 3):
            return "P1"
        return "P2"

    def block_incomplete(self, block: dict) -> bool:
        """Неполная выгрузка: усечение или незавершённая пагинация."""
        if not isinstance(block, dict):
            return False
        return bool(block.get("truncated")) or block.get("pagination_complete") is False

    def source_incomplete(self, action: str) -> bool:
        for item in (self.ctx.get("collected") or {}).get("sources") or []:
            if item.get("action") != action:
                continue
            if item.get("truncated") is True or item.get("pagination_complete") is False:
                return True
        return False

    def segment_compatible(self, block: dict) -> tuple[bool, str]:
        """Совместим ли сегментный CPA с общим: период, охват, цель, атрибуция."""
        total_cost = block.get("total", {}).get("cost")
        campaign_cost = self.period_cost()
        if total_cost is None or campaign_cost is None:
            return False, "нет расхода сегмента или кампании"
        deviation = abs(total_cost - campaign_cost) / campaign_cost if campaign_cost else 1
        if deviation > self.th.get("tolerance_segment_sum", 0.05):
            return False, (f"сумма расхода сегментов {fmt_money(total_cost)} расходится "
                           f"с кампанией {fmt_money(campaign_cost)} — другой охват или период")
        if not self.ctx["goal"].get("configured"):
            return False, "основная цель не задана — CPA сегмента относится ко всем целям"
        return True, "период и охват совпадают"

    # ------------------------------------------------------------- правила
    def run(self) -> dict:
        self.rule_campaign_state()
        self.rule_tracking()
        self.rule_moderation()
        self.rule_extensions()
        self.rule_strategy()
        self.rule_dynamics()
        self.rule_segments()
        if self.enabled("semantic"):
            self.rule_search_queries()
            self.rule_keywords()
            self.rule_negatives_hygiene()
        if self.enabled("rsy"):
            self.rule_placements()
        if self.enabled("funnel"):
            self.rule_funnel()
        if self.enabled("growth"):
            self.rule_growth()
        if self.enabled("history"):
            self.rule_history()
        if self.enabled("monitor"):
            self.rule_monitor()
        # План эксперимента выводится из гипотез и не зависит от выбранного режима.
        self.rule_experiments()
        return {
            "findings": self.findings,
            "recommendations": self.recommendations,
            "hypotheses": self.hypotheses,
            "growth": self.growth,
            "limitations": self.limitations,
        }

    # --- состояние кампании ------------------------------------------------
    def rule_campaign_state(self):
        campaign = self.ctx["campaign"]
        state = str(campaign.get("state") or "").upper()
        if state not in ("ARCHIVED", "OFF", "SUSPENDED"):
            return
        labels = {"ARCHIVED": "в архиве", "OFF": "выключена", "SUSPENDED": "приостановлена"}
        clarification = campaign.get("raw", {}).get("StatusClarification") if isinstance(
            campaign.get("raw"), dict) else None

        current = self.ctx["metrics"]["a"] or {}
        known = current.get("impressions") is not None or current.get("cost") is not None
        zero = (known and not current.get("impressions") and not current.get("clicks")
                and not current.get("cost"))
        if zero:
            suffix = "— показов нет"
            why = (f"Кампания {labels.get(state, state)}: показов и расхода за период нет, "
                   "статистика это подтверждает.")
        elif known:
            suffix = "— текущих показов нет"
            why = (f"Кампания {labels.get(state, state)}: в текущих показах не участвует, "
                   f"но за период есть данные ({current.get('impressions')} показов).")
        else:
            suffix = "— статистика за период недоступна"
            why = (f"Кампания {labels.get(state, state)}: в текущих показах не участвует. "
                   "Данные о показах и расходе за период не получены, поэтому утверждать "
                   "нулевой расход нельзя.")

        finding = self.finding(
            rule="CMP-01", category="campaign_state",
            title=f"Кампания {labels.get(state, state)} {suffix}",
            obj={"type": "campaign", "id": self.session["campaign_id"],
                 "path": object_path(("campaign", self.session["campaign_id"]))},
            evidence=[{"source": "campaigns_get", "field": "State", "value": state},
                      {"source": "campaigns_get", "field": "StatusClarification",
                       "value": clarification or "—"}],
            metrics={"state": state, "stats_known": known},
            priority="P1" if state == "ARCHIVED" else "P0",
            confidence="high",
            why=why,
            next_step=("Восстановить кампанию из архива, если она нужна, и запустить аудит заново."
                       if state == "ARCHIVED"
                       else "Включить кампанию или снять приостановку и перепроверить статистику."),
        )
        self.recommend(
            finding, problem=f"Кампания {labels.get(state, state)}",
            change=("Восстановить из архива и запустить заново, если кампания должна работать."
                    if state == "ARCHIVED" else "Включить кампанию и дождаться показов."),
            effect={"type": "technical", "value": None, "unit": None,
                    "method": "подтверждённое состояние кампании", "conditions": []},
            risk={"level": "medium",
                  "notes": ["восстановление сбросит обучение стратегии",
                            "без бюджета и целей показы не начнутся"]},
            apply={"method": "manual", "tool": None,
                   "manual": "Директ → кампании → восстановить из архива / включить"},
            verify=self.verify_template(["показы", "расход"], direction="появление трафика",
                                       success="кампания показывает и расходует бюджет",
                                       failure="показов нет — проверить статус и оплату"),
        )

    # --- отслеживание ------------------------------------------------------
    def rule_tracking(self):
        campaign = self.ctx["campaign"]
        counters = campaign.get("counters") or []
        if not counters:
            finding = self.finding(
                rule="TRK-01", category="tracking", title="Счётчик Метрики не привязан к кампании",
                obj={"type": "campaign", "id": self.session["campaign_id"],
                     "path": object_path(("campaign", self.session["campaign_id"]))},
                evidence=[{"source": "campaigns_get", "field": "Counters", "value": "—"}],
                metrics={"counters": 0},
                priority="P0", confidence="high",
                why="В настройках кампании нет счётчика Метрики: конверсии и CPA не могут считаться корректно.",
                next_step="Привязать счётчик в интерфейсе кампании и повторить аудит.",
            )
            self.recommend(
                finding, problem="Кампания без счётчика Метрики",
                change="Привязать рабочий счётчик Метрики к кампании (вручную в интерфейсе Директа).",
                effect={"type": "technical", "value": None, "unit": None,
                        "method": "подтверждённый факт настройки", "conditions": []},
                risk={"level": "low", "notes": ["смена счётчика может разорвать историю конверсий"]},
                apply={"method": "manual", "tool": None,
                       "manual": "Директ → кампания → Счётчик Метрики"},
                verify=self.verify_template(["CPA", "конверсии"], direction="появление данных",
                                           success="конверсии и CPA появились в отчёте",
                                           failure="данных нет — проверить установку счётчика"),
                priority="P0",
            )
        else:
            self.finding(
                rule="TRK-00", category="tracking", title="Счётчик Метрики привязан",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "campaigns_get", "field": "Counters", "value": ", ".join(counters)}],
                metrics={"counters": len(counters)}, priority="P3",
                why="Счётчик указан в настройках кампании.",
            )

        if not self.ctx["goal"].get("configured"):
            finding = self.finding(
                rule="TRK-02", category="tracking", title="Основная цель не задана",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "stats_compare", "field": "meta.context",
                           "value": self.ctx["goal"].get("context", "")[:300]}],
                metrics={"goal_id": None},
                priority="P0", confidence="high",
                why="Без основной цели CPA считается по всем целям сразу, и оптимизация стратегии может идти на служебные действия.",
                next_step="Назначить основную цель кампании (или PurposeGoals на аккаунте) и пересчитать аудит.",
            )
            self.recommend(
                finding, problem="CPA не относится к одной цели",
                change="Назначить основную цель кампании, соответствующую продаже, а не служебным действиям.",
                effect={"type": "technical", "value": None, "unit": None,
                        "method": "устраняет неопределённость метрики", "conditions": []},
                risk={"level": "medium", "notes": ["смена цели влияет на обучение стратегии"]},
                apply={"method": "manual", "tool": None,
                       "manual": "Директ → кампания → Основная цель / Метрика → цель"},
                verify=self.verify_template(["CPA", "конверсии"], direction="стабильная цель",
                                           success="CPA считается по одной цели и сопоставим между периодами"),
            )

        metrika = self.ctx["segments"].get("metrika") or {}
        rows = metrika.get("rows") or []
        if not rows:
            self.limit("Данные Метрики недоступны: сверка кликов и визитов не выполнялась", group="funnel")
            return
        row = rows[0]
        ctv = row.get("clicks_to_visits")
        clicks, visits = row.get("clicks"), row.get("visits")
        if ctv is None and clicks and visits:
            ctv = visits / clicks * 100
        if ctv is not None and ctv < 70:
            finding = self.finding(
                rule="TRK-03", category="tracking",
                title="Визитов заметно меньше кликов",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "metrika_direct_cpa", "field": "ClicksToVisits", "value": round(ctv, 1)},
                          {"source": "metrika_direct_cpa", "field": "Clicks", "value": int(clicks or 0)},
                          {"source": "metrika_direct_cpa", "field": "Visits", "value": int(visits or 0)}],
                metrics={"clicks": clicks, "visits": visits, "clicks_to_visits_pct": ctv},
                threshold={"name": "минимальная доля визитов", "value": 70, "unit": "percent"},
                classification="likely_cause",
                priority="P0", confidence="medium",
                why=f"Визиты составляют {ctv:.1f}% кликов. Это может быть следствием разметки, "
                    "медленной загрузки, блокировщиков или редиректов.",
                alternatives=["часть кликов не доходит из-за скорости сайта",
                              "счётчик установлен не на всех страницах",
                              "потеря UTM или редирект между доменами"],
                next_step="Проверить отчёт «Источники → Директ» в Метрике и тестовый визит с меткой.",
            )
            self.recommend(
                finding, problem="Разрыв между кликами и визитами",
                change="Проверить разметку ссылок, редиректы и загрузку страницы на мобильных; "
                       "сверить тестовый клик в Метрике.",
                effect={"type": "qualitative", "value": None, "unit": None,
                        "method": "диагностика", "conditions": ["нужен доступ к сайту и счётчику"]},
                risk={"level": "low", "notes": ["правки на сайте вне зоны MCP-аудита"]},
                apply={"method": "manual", "tool": None, "manual": "сайт / Метрика / Директ"},
                verify=self.verify_template(["визиты", "доля визитов от кликов"], direction="рост доли визитов",
                                           success="доля визитов выросла до 90%+ при том же трафике"),
            )
        else:
            self.finding(
                rule="TRK-04", category="tracking", title="Клики и визиты сопоставимы",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "metrika_direct_cpa", "field": "ClicksToVisits", "value": ctv}],
                metrics={"clicks_to_visits_pct": ctv}, priority="P3",
                why="Разрыв между кликами и визитами в пределах нормы.",
            )

    # --- модерация ---------------------------------------------------------
    def rule_moderation(self):
        rows = self.ctx["source_rows"].get("moderation_check") or []
        if not rows:
            return
        rejected = []
        for row in rows:
            status = str(row.get("Status") or row.get("Статус") or "").upper()
            if status in ("REJECTED", "PARTIALLY_REJECTED") or row.get("RejectReason"):
                rejected.append(row)
        if not rejected:
            return
        for row in rejected[:10]:
            obj_id = fmt_id(row.get("Id") or row.get("AdId") or "—")
            reason = row.get("RejectReason") or row.get("StatusClarification") or row.get("Причина") or "причина не указана"
            finding = self.finding(
                rule="MOD-01", category="moderation", title="Объявление или фраза отклонены модерацией",
                obj={"type": "ad", "id": obj_id, "path": object_path(("campaign", self.session["campaign_id"]), ("ad", obj_id))},
                evidence=[{"source": "moderation_check", "field": "Status", "value": row.get("Status") or row.get("Статус")},
                          {"source": "moderation_check", "field": "Reason", "value": reason}],
                metrics={"status": row.get("Status")}, priority="P0", confidence="high",
                why=f"Модерация отклонила объект: {reason}.",
                next_step="Исправить текст/ссылку и отправить на повторную модерацию.",
            )
            self.recommend(
                finding, problem=f"Отклонён объект {obj_id}",
                change=f"Устранить причину отклонения: {reason}.",
                effect={"type": "technical", "value": None, "unit": None,
                        "method": "подтверждённый статус модерации", "conditions": []},
                risk={"level": "low", "notes": ["повторное отклонение при неустранённой причине"]},
                apply={"method": "tool", "tool": "ads_update", "manual": None},
                verify=self.verify_template(["статус модерации"], direction="ACCEPTED",
                                           success="объект прошёл модерацию",
                                           failure="отклонён повторно — причина не устранена"),
                priority="P0",
            )

    # --- объявления --------------------------------------------------------
    def rule_extensions(self):
        rows = self.ctx["source_rows"].get("ads_list") or []
        if not rows:
            self.limit("ads_list не получен: полнота объявлений и расширений не проверялась", group="semantic")
            return
        if self.source_incomplete("ads_list"):
            self.limit("ads_list: выгрузка неполная — выводы по объявлениям не формируются",
                       group="semantic")
            return
        columns = set(rows[0].keys())
        # Визитка (VCard) удалена Яндексом и через API не заполняется — не требуем её.
        labels = {
            "displayurlpath": "отображаемая ссылка",
            "sitelink": "быстрые ссылки",
            "callout": "уточнения",
            "image": "изображения",
        }
        from .extract import network_enabled
        strategy = self.ctx.get("strategy") or {}
        if strategy.get("truncated"):
            labels.pop("image", None)
            self.limit("Тип размещения в строке стратегии обрезан: изображения не проверялись",
                       group="semantic")
        elif not network_enabled(strategy):
            labels.pop("image", None)
            self.limit("Изображения не проверялись: кампания не работает в сетях, "
                       "где они обязательны", group="semantic")
        interesting = {}
        for column in columns:
            key = str(column).lower()
            for token, label in labels.items():
                if token in key and "vcard" not in key:
                    interesting[column] = label
        if not interesting:
            self.limit("В ответе ads_list нет полей расширений: проверка объявлений ограничена",
                       group="semantic", detail=f"колонки: {', '.join(sorted(map(str, columns)))[:300]}")
            return
        incomplete = []
        for row in rows:
            missing = [column for column in interesting
                       if row.get(column) in (None, "", "—", "[]")]
            if missing:
                incomplete.append((row, missing))
        if not incomplete:
            return
        row, missing = incomplete[0]
        obj_id = fmt_id(row.get("Id") or row.get("AdId") or "—")
        missing_labels = sorted({interesting[column] for column in missing})
        finding = self.finding(
            rule="EXT-01", category="ads", title="В объявлениях не заполнены расширения",
            obj={"type": "ad", "id": obj_id, "path": object_path(("campaign", self.session["campaign_id"]), ("ad", obj_id))},
            evidence=[{"source": "ads_list", "field": "missing", "value": ", ".join(sorted(missing))},
                      {"source": "ads_list", "field": "ads_checked", "value": len(rows)}],
            metrics={"ads_with_gaps": len(incomplete), "ads_checked": len(rows),
                     "missing_fields": sorted(missing)},
            priority="P2", confidence="high",
            why=f"У {len(incomplete)} из {len(rows)} объявлений не заполнено: "
                f"{', '.join(missing_labels)}. Это снижает площадь объявления, "
                "но не гарантирует рост конверсий.",
            next_step=f"Дописать: {', '.join(missing_labels)}. Проверить отображение на мобильном.",
        )
        self.recommend(
            finding, problem="Неполные расширения объявлений",
            change=f"Дописать: {', '.join(missing_labels)}; тексты — по лимитам Директа.",
            effect={"type": "qualitative", "value": None, "unit": None,
                    "method": "практика CTR, не гарантия", "conditions": ["сохранить релевантность объявления"]},
            risk={"level": "low", "notes": ["рост CTR может привести к другому составу трафика"]},
            apply={"method": "tool", "tool": "ads_update", "manual": "extensions_create"},
            verify=self.verify_template(["CTR", "CR", "CPA"], direction="CTR вверх, CPA не хуже",
                                       side_effects=["изменение качества трафика"]),
        )

    # --- стратегия ---------------------------------------------------------
    def rule_strategy(self):
        strategy = self.ctx.get("strategy") or {}
        campaign = self.ctx["campaign"]
        if strategy.get("raw") and strategy.get("raw") != "—":
            self.limit("Состояние обучения стратегии через API не читается: "
                       "выводы о работе алгоритма не делаются", group="strategy")
        start = campaign.get("start_date")
        period = self.ctx["periods"]["a"]
        if start and period.get("from"):
            try:
                start_d = date.fromisoformat(str(start)[:10])
                from_d = date.fromisoformat(period["from"])
                days = (from_d - start_d).days
            except ValueError:
                days = None
            if days is not None and 0 <= days < 14:
                self.finding(
                    rule="STR-02", category="strategy",
                    title="Кампания могла быть в обучении",
                    obj={"type": "campaign", "id": self.session["campaign_id"]},
                    evidence=[{"source": "campaigns_get", "field": "StartDate", "value": start},
                              {"source": "session", "field": "period_a_from", "value": period["from"]}],
                    metrics={"days_since_start": days},
                    classification="hypothesis", priority="P3", confidence="low",
                    why=f"Кампания запущена за {days} дн. до начала периода: часть периода могла приходиться на обучение стратегии.",
                    alternatives=["изменение ставок или бюджета", "сезонность", "изменение спроса"],
                    next_step="Сравнить динамику после выхода из обучения (обычно 1–2 недели) и не менять настройки в этот период.",
                )
        if strategy.get("search") == "OFF" and strategy.get("network"):
            self.finding(
                rule="STR-03", category="strategy", title="Кампания работает только в сетях",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "campaigns_get", "field": "Strategy", "value": strategy.get("raw", "")[:300]}],
                metrics={"search": "OFF", "network": strategy.get("network")},
                priority="P3", confidence="high",
                why="Поиск отключён: правила поисковых запросов к кампании не применяются.",
            )

    # --- динамика ----------------------------------------------------------
    def rule_dynamics(self):
        if self.blocked("dynamics"):
            self.limit("Динамика не анализировалась: не хватает сопоставимых данных двух периодов", group="dynamics")
            return
        conv = self.cmp.get("conversions") or {}
        if not conv.get("available"):
            return
        if conv.get("base_zero"):
            self.finding(
                rule="DYN-00", category="dynamics", title="В прошлом периоде не было конверсий",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "stats_compare", "field": "Конверсии", "value": self.a.get("conversions")}],
                metrics={"a": self.a.get("conversions"), "b": 0},
                priority="P3", confidence="high",
                why="Относительное изменение не считается: база B равна нулю.",
                next_step="Сравнивать абсолютные значения и CPA, а не проценты.",
            )
            return

        delta = conv.get("abs") or 0
        if abs(conv.get("pct") or 0) < (self.th.get("alert_sensitivity", 0.2) * 100):
            return

        # Разложение: что дало изменение конверсий.
        drivers = []
        for key, label in (("impressions", "показы"), ("ctr", "CTR"), ("cr", "CR")):
            change = (self.cmp.get(key) or {}).get("pct")
            if change is not None:
                drivers.append({"metric": label, "delta_pct": change})
        cr_change = (self.cmp.get("cr") or {}).get("pct")
        cpc_change = (self.cmp.get("cpc") or {}).get("pct")
        cpa_change = (self.cmp.get("cpa") or {}).get("pct")

        sufficient = met.sufficiency(self.a.get("impressions"), self.a.get("clicks"),
                                     self.a.get("conversions"), self.th)
        significance = met.two_proportion_z(
            int(self.a.get("conversions") or 0), int(self.a.get("clicks") or 0),
            int(self.b.get("conversions") or 0), int(self.b.get("clicks") or 0))
        z_text = ("z={z:.2f}, p={p:.3f}".format(z=significance["z"], p=significance["p"])
                  if significance.get("valid") else "тест не применим: " + str(significance.get("reason")))
        confirmed = bool(significance.get("valid") and significance.get("p") is not None
                         and significance["p"] < 0.05)
        if not sufficient["sufficient"]:
            self.finding(
                rule="STAT-01", category="statistics", title="Изменение конверсий в пределах шума",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "stats_compare", "field": "Конверсии",
                           "value": f"{self.b.get('conversions')} → {self.a.get('conversions')}"}],
                metrics={"a": self.a.get("conversions"), "b": self.b.get("conversions"),
                         "gaps": sufficient["gaps"]},
                threshold={"name": "минимальный объём", "value": sufficient["thresholds"], "unit": "counts"},
                classification="hypothesis", priority="P3", confidence="low",
                why="Объёма данных недостаточно: " + "; ".join(sufficient["gaps"]) + ".",
                alternatives=["случайные колебания", "сезонность", "изменение состава трафика"],
                next_step="Наблюдать ещё 1–2 недели, не менять настройки.",
            )
            self.hypothesis(
                title="Изменение конверсий не доказано",
                statement="Отклонение есть, но объёма данных недостаточно, чтобы отличить его от шума.",
                basis=[f"конверсии {self.b.get('conversions')} → {self.a.get('conversions')}",
                       *sufficient["gaps"]],
                alternatives=["случайные колебания", "сезонность", "состав трафика"],
                check="Набрать объём: кликов ≥ "
                      f"{sufficient['thresholds']['min_clicks']}, конверсий ≥ "
                      f"{sufficient['thresholds']['min_conversions']}, затем пересчитать.",
            )
            return

        main_driver = "CR" if cr_change is not None and abs(cr_change) > 10 else "показы/CTR"
        finding = self.finding(
            rule="DYN-01", category="dynamics",
            title=f"Конверсии изменились на {conv['pct']:+.1f}%",
            obj={"type": "campaign", "id": self.session["campaign_id"]},
            evidence=[
                {"source": "stats_compare", "field": "Конверсии",
                 "value": f"{self.b.get('conversions')} → {self.a.get('conversions')}"},
                {"source": "stats_compare", "field": "CTR",
                 "value": f"{(self.b.get('ctr') or 0):.2f} → {(self.a.get('ctr') or 0):.2f}"},
                {"source": "stats_compare", "field": "CR",
                 "value": f"{(self.b.get('cr') or 0):.2f} → {(self.a.get('cr') or 0):.2f}"},
                {"source": "stats_compare", "field": "CPA",
                 "value": f"{fmt_money(self.b.get('cpa'))} → {fmt_money(self.a.get('cpa'))}"},
                {"source": "stats_compare", "field": "проверка значимости", "value": z_text},
            ],
            metrics={
                "conversions_a": self.a.get("conversions"), "conversions_b": self.b.get("conversions"),
                "delta_pct": conv["pct"], "ctr_pct_delta": (self.cmp.get("ctr") or {}).get("pct"),
                "cr_pct_delta": cr_change, "cpc_pct_delta": cpc_change, "cpa_pct_delta": cpa_change,
                "z": significance.get("z"), "p": significance.get("p"), "significant": confirmed,
            },
            threshold={"name": "порог предупреждения", "value": self.th.get("alert_sensitivity", 0.2) * 100,
                       "unit": "percent"},
            classification="likely_cause",
            priority="P1" if confirmed else "P2",
            confidence="medium" if confirmed else "low",
            why=(f"Факт: конверсии {self.b.get('conversions')} → {self.a.get('conversions')} "
                 f"({conv['pct']:+.1f}%), CPA {fmt_money(self.b.get('cpa'))} → "
                 f"{fmt_money(self.a.get('cpa'))}. Арифметически основной вклад даёт {main_driver}. "
                 + ("Различие статистически подтверждено (" + z_text + ")." if confirmed
                    else "Различие статистически НЕ подтверждено (" + z_text + "): "
                         "это может быть колебанием, вывод осторожный.")),
            alternatives=[
                "изменение спроса или сезонность",
                "другой состав трафика (запросы, площадки, регионы)",
                "изменения на сайте или в форме заявки",
                "задержка отражения конверсий",
                "изменение ставок или стратегии в этот период",
            ],
            next_step="Проверить журнал изменений и сегменты (устройства, площадки, запросы) за тот же период.",
            spend={"actual": None, "at_risk": None, "potential_saving": None},
        )

        self.hypothesis(
            title="Причина падения конверсий не установлена",
            statement="Снижение конверсий согласуется с падением CR, но причинность не доказана.",
            basis=[f"CR: {(self.b.get('cr') or 0):.2f}% → {(self.a.get('cr') or 0):.2f}%",
                   f"CTR: {(self.b.get('ctr') or 0):.2f}% → {(self.a.get('ctr') or 0):.2f}%"],
            alternatives=["спрос", "состав трафика", "сайт/форма", "трекинг"],
            check="Сравнить те же метрики по устройствам и площадкам; исключить изменения в журнале кампании.",
            priority="P2", source_finding=finding["id"],
        )
        self.recommend(
            finding, problem="Конверсии и CPA изменились против прошлого периода",
            change="Не менять настройки вслепую: сначала проверить журнал изменений и сегменты за тот же период.",
            effect={"type": "none", "value": None, "unit": None,
                    "method": "диагностика вместо изменения", "conditions": []},
            risk={"level": "low", "notes": ["поспешная правка ставок может усилить отклонение"]},
            apply={"method": "manual", "tool": None, "manual": "анализ, без изменений"},
            verify=self.verify_template(["конверсии", "CPA", "CR"], direction="возврат к базовому уровню",
                                       success="показатели вернулись к уровню B без правок или после точечной правки",
                                       failure="отклонение сохраняется — причина не найдена"),
        )

    # --- сегменты ----------------------------------------------------------
    def rule_segments(self):
        if self.blocked("segments"):
            self.limit("Сегментный анализ не выполнялся: данные сегментов недоступны или несопоставимы")
            return
        average_cpa = self.a.get("cpa")
        threshold = self.th.get("attention_threshold_rub")
        tests = 0
        segments_checked = []
        for name in ("devices", "regions"):
            block = self.ctx["segments"].get(name) or {}
            if not block.get("available") or not block.get("rows"):
                continue
            if self.block_incomplete(block):
                self.limit(f"Сегменты «{name}»: выгрузка неполная — рекомендации не формируются",
                           group="segments")
                segments_checked.append({"segment": name, "comparable": False,
                                         "reason": "неполная выгрузка"})
                continue
            compatible, reason = self.segment_compatible(block)
            if not compatible:
                self.finding(
                    rule="SEG-00", category="segments",
                    title=f"Сегменты «{name}» несопоставимы с итогом",
                    obj={"type": "segment_group", "id": name},
                    evidence=[{"source": block.get("action"), "field": "total.cost",
                               "value": block.get("total", {}).get("cost")}],
                    metrics={"segment_cost": block.get("total", {}).get("cost"),
                             "campaign_cost": self.period_cost()},
                    classification="fact", priority="P3", confidence="medium",
                    why=f"Сравнение CPA недоступно: {reason}.",
                    next_step="Запросить сегменты за тот же период и с той же целью.",
                )
                segments_checked.append({"segment": name, "comparable": False, "reason": reason})
                continue
            segments_checked.append({"segment": name, "comparable": True, "reason": reason})
            for row in block["rows"]:
                if row.get("cpa") is None or row.get("conversions") in (None, 0):
                    continue
                tests += 1
                if threshold and row["cpa"] > threshold:
                    priority = "P2" if row["cpa"] > average_cpa * 2 else "P3"
                    obj_type = "device" if name == "devices" else "region"
                    finding = self.finding(
                        rule="SEG-01", category="segments",
                        title=f"CPA сегмента выше среднего: {row['key']}",
                        obj={"type": obj_type, "id": row["key"],
                             "path": object_path(("campaign", self.session["campaign_id"]), (obj_type, row["key"]))},
                        evidence=[{"source": block["action"], "field": "CPA", "value": row["cpa"]},
                                  {"source": "stats_compare", "field": "CPA", "value": average_cpa}],
                        metrics={"segment_cpa": row["cpa"], "campaign_cpa": average_cpa,
                                 "segment_cost": row["cost"], "segment_conversions": row["conversions"]},
                        threshold={"name": "2 × CPA", "value": threshold, "unit": "RUB"},
                        classification="fact",
                        priority=priority, confidence="medium",
                        why=f"CPA сегмента {fmt_money(row['cpa'])} против среднего {fmt_money(average_cpa)}. "
                            f"Источник сегмента: {block['action']}, период и охват совпадают с итогом.",
                        alternatives=["мало конверсий в сегменте", "разная цена клика", "состав аудитории"],
                        next_step="Проверить объём: при малом числе конверсий вывод не делать.",
                        spend={"actual": row["cost"], "at_risk": None, "potential_saving": None},
                    )
                    self.recommend(
                        finding, problem=f"Дорогой сегмент: {row['key']}",
                        change="Рассмотреть корректировку ставки для сегмента после проверки объёма данных.",
                        effect={"type": "qualitative", "value": None, "unit": None,
                                "method": "снижение ставки не гарантирует снижение CPA",
                                "conditions": ["сегмент устойчиво дороже 2 периодов"]},
                        risk={"level": "medium", "notes": ["снижение ставки уменьшит показы и может уменьшить заявки"]},
                        apply={"method": "tool", "tool": "bid_modifiers_set", "manual": None},
                        verify=self.verify_template(["CPA сегмента", "конверсии сегмента"],
                                                   direction="CPA вниз, конверсии не падают",
                                                   side_effects=["потеря охвата"]),
                    )
        if tests > 1:
            adjustment = met.adjust_alpha(0.05, tests)
            self.limit(f"Сегментных сравнений: {tests}. Порог значимости с поправкой "
                       f"Бонферрони: {adjustment['alpha_adjusted']:.4f}",
                       group="segments", detail="множественные сравнения повышают риск ложных срабатываний")
        self.ctx["segments_checked"] = segments_checked

    # --- поисковые запросы -------------------------------------------------
    def rule_search_queries(self):
        block = self.ctx["segments"].get("search_queries") or {}
        product = self.session.get("product") or {}
        if not block.get("available") or not block.get("rows"):
            reason = block.get("note") or "источник не получен"
            self.limit(f"Поисковые запросы не анализировались: {reason}", group="semantic")
            return
        if self.block_incomplete(block):
            self.limit("Поисковые запросы: выгрузка неполная — выводы и минус-фразы "
                       "не формируются", group="semantic")
            return
        keywords = [row.get("key") for row in (self.ctx["segments"].get("keywords") or {}).get("rows", [])]
        self.limit("Покрытие запросов ключевыми фразами не проверяется: в stats_search_queries "
                   "нет привязки запроса к фразе", group="semantic")
        protected = self.th.get("protected_words") or []
        exclude_terms = product.get("exclude_terms") or []
        min_spend = self.th.get("min_spend_no_conversion_rub") or 1000
        has_context = bool(product.get("what"))

        for row in block["rows"]:
            cost = row.get("cost") or 0
            conversions = row.get("conversions")
            if conversions is None:
                continue
            intent = classify_intent(row["key"], product_terms=product)

            if conversions == 0 and cost >= min_spend:
                finding = self.finding(
                    rule="SQ-01", category="search_queries",
                    title=f"Запрос без конверсий: «{row['key']}»",
                    obj={"type": "search_query", "id": row["key"],
                         "path": object_path(("campaign", self.session["campaign_id"]), ("query", row["key"]))},
                    evidence=[{"source": block["action"], "field": "Cost", "value": cost},
                              {"source": block["action"], "field": "Conversions", "value": conversions},
                              {"source": "intent", "field": "class", "value": intent["intent"]},
                              {"source": "intent", "field": "reason", "value": intent["reason"]}],
                    metrics={"cost": cost, "conversions": 0, "clicks": row.get("clicks"),
                             "intent": intent["intent"], "intent_reason": intent["reason"]},
                    threshold={"name": "минимальный расход без конверсий", "value": min_spend, "unit": "RUB"},
                    classification="fact",
                    priority=self.waste_priority(cost, "medium" if not has_context else "high"),
                    confidence="high" if has_context else "medium",
                    why=f"Запрос израсходовал {fmt_money(cost)} и не дал конверсий. "
                        f"Класс запроса: {intent['intent']} ({intent['reason']}).",
                    alternatives=["конверсия могла не дойти из-за трекинга",
                                  "запрос относится к другому этапу воронки",
                                  "малый объём для вывода"],
                    next_step="Проверить, есть ли по запросу заявки в Метрике и CRM за тот же период.",
                    spend={"actual": cost, "at_risk": cost, "potential_saving": None},
                )
                if intent["intent"] == "irrelevant" and has_context:
                    converting = [r["key"] for r in block["rows"] if (r.get("conversions") or 0) > 0]
                    suggestion = suggest_negatives(
                        row["key"], keywords=keywords, protected_words=protected,
                        exclude_terms=exclude_terms or [], converting_queries=converting)
                    usable = suggestion["usable"]
                    if usable:
                        candidate = usable[0]["candidate"]
                        self.recommend(
                            finding, problem=f"Нерелевантный запрос тратит бюджет: «{row['key']}»",
                            change=f"Добавить минус-фразу «{candidate}» на уровне кампании или группы "
                                   f"после проверки в прогнозе Директа.",
                            effect={"type": "estimate", "value": cost, "unit": "RUB/период",
                                    "method": "расход запроса за период A при полном исключении",
                                    "conditions": ["минус-фраза действительно блокирует запрос",
                                                   "другие запросы не потеряны"]},
                            risk={"level": "medium",
                                  "notes": ["приблизительное совпадение по основе слова",
                                            "минус может отсечь похожие коммерческие запросы"]},
                            apply={"method": "tool", "tool": "negatives_set", "manual": None},
                            verify=self.verify_template(["расход на запрос", "конверсии", "CPA"],
                                                       direction="расход вниз, конверсии не падают",
                                                       side_effects=["потеря части трафика"]),
                        )
                    else:
                        self.hypothesis(
                            title=f"Минус-фраза для «{row['key']}» не подобрана автоматически",
                            statement=suggestion["reason"],
                            basis=[f"расход {fmt_money(cost)}", "0 конверсий",
                                   f"интент: {intent['intent']}"],
                            alternatives=["запрос нужен на другом этапе воронки",
                                          "конверсии не фиксируются"],
                            check="Сверить запрос с продуктом и прогнозом Директа вручную.",
                            source_finding=finding["id"],
                        )
                        self.recommend(
                            finding, problem=f"Расход без конверсий: «{row['key']}»",
                            change="Проверить запрос вручную: автоматическая минус-фраза не подобрана "
                                   "без риска для целевого трафика.",
                            effect={"type": "none", "value": None, "unit": None,
                                    "method": "нужно решение человека", "conditions": []},
                            risk={"level": "medium",
                                  "notes": ["минус-фраза наугад может отсечь коммерческий трафик"]},
                            apply={"method": "manual", "tool": None,
                                   "manual": "проверить запрос и прогноз Директа"},
                            verify=self.verify_template(["расход", "конверсии"], direction="наблюдение"),
                        )
                elif intent["intent"] == "informational":
                    self.hypothesis(
                        title=f"Информационный запрос «{row['key']}»",
                        statement="Информационный запрос не считается мусором автоматически: он может быть частью пути к заявке.",
                        basis=[f"расход {fmt_money(cost)}", "0 конверсий в модели последнего клика"],
                        alternatives=["запрос участвует в цепочке касаний",
                                      "конверсия придёт позже"],
                        check="Посмотреть assisted conversions и путь к конверсии в Метрике.",
                        source_finding=finding["id"],
                    )
                else:
                    self.recommend(
                        finding, problem=f"Расход без конверсий: «{row['key']}»",
                        change="Наблюдать или понизить приоритет запроса; не минусовать без контекста продукта.",
                        effect={"type": "none", "value": None, "unit": None,
                                "method": "решение требует данных о продукте", "conditions": []},
                        risk={"level": "medium", "notes": ["минус-фраза без контекста может отсечь целевой трафик"]},
                        apply={"method": "manual", "tool": None,
                               "manual": "проверить запрос вручную перед любыми правками"},
                        verify=self.verify_template(["расход", "конверсии"], direction="наблюдение"),
                    )

            elif conversions and conversions > 0:
                cpa = row.get("cpa")
                campaign_cpa = self.a.get("cpa")
                if cpa and campaign_cpa and cpa < campaign_cpa * 0.7:
                    self.growth.append({
                        "id": stable_id("G", "query", row["key"])[:40],
                        "type": "search_intent",
                        "object": {"type": "search_query", "id": row["key"]},
                        "evidence": [{"source": block["action"], "field": "CPA", "value": cpa},
                                     {"source": "stats_compare", "field": "CPA", "value": campaign_cpa}],
                        "why": f"Запрос даёт CPA {fmt_money(cpa)} против среднего {fmt_money(campaign_cpa)}.",
                        "confidence": "medium",
                        "action": "Проверить, вынесен ли запрос в отдельную фразу или группу.",
                        "promise": "рост заявок не гарантируется",
                    })

    # --- ключевые фразы ----------------------------------------------------
    def rule_keywords(self):
        block = self.ctx["segments"].get("keywords") or {}
        if not block.get("available") or not block.get("rows"):
            self.limit("Ключевые фразы не анализировались: источник недоступен", group="semantic")
            return
        if self.block_incomplete(block):
            self.limit("Ключевые фразы: выгрузка неполная — выводы не формируются",
                       group="semantic")
            return
        min_spend = self.th.get("min_spend_no_conversion_rub") or 1000
        seen = {}
        for row in block["rows"]:
            key = (row.get("key") or "").strip().lower()
            if not key:
                continue
            seen.setdefault(key, []).append(row)
        for key, rows in seen.items():
            if len(rows) > 1:
                self.finding(
                    rule="KW-02", category="keywords", title="Фраза встречается в нескольких группах",
                    obj={"type": "keyword", "id": key},
                    evidence=[{"source": block["action"], "field": "rows", "value": len(rows)}],
                    metrics={"occurrences": len(rows), "clicks": sum(r.get("clicks") or 0 for r in rows)},
                    priority="P3", confidence="high",
                    why="Одинаковая фраза в разных группах создаёт внутреннюю конкуренцию и мешает оценке.",
                    next_step="Оставить фразу в одной группе, остальные закрыть или переформулировать.",
                )
        for row in block["rows"]:
            cost = row.get("cost") or 0
            if row.get("conversions") == 0 and cost >= min_spend:
                self.finding(
                    rule="KW-01", category="keywords", title=f"Фраза без конверсий: «{row.get('key')}»",
                    obj={"type": "keyword", "id": row.get("key"),
                         "path": object_path(("campaign", self.session["campaign_id"]), ("keyword", row.get("key")))},
                    evidence=[{"source": block["action"], "field": "Cost", "value": cost},
                              {"source": block["action"], "field": "Conversions", "value": 0}],
                    metrics={"cost": cost, "clicks": row.get("clicks"), "conversions": 0},
                    threshold={"name": "минимальный расход без конверсий", "value": min_spend, "unit": "RUB"},
                    priority=self.waste_priority(cost, "medium"), confidence="medium",
                    why=f"Фраза израсходовала {fmt_money(cost)} без конверсий. "
                        "Пауза или переработка — решение после проверки объёма.",
                    alternatives=["конверсии приходят с задержкой", "фраза нужна для охвата"],
                    next_step="Сверить с поисковыми запросами: возможно, мусор внутри фразы, а не сама фраза.",
                    spend={"actual": cost, "at_risk": cost, "potential_saving": None},
                )

    # --- гигиена минус-фраз ------------------------------------------------
    def rule_negatives_hygiene(self):
        block = self.ctx.get("negatives") or {}
        if not block.get("available"):
            self.limit("Минус-фразы не проверялись: negatives_audit недоступен", group="semantic")
            return
        keywords = [row.get("key") for row in (self.ctx["segments"].get("keywords") or {}).get("rows", [])]
        if not keywords:
            return
        conflicts = []
        ignored = []
        for entry in block.get("campaign", []) + block.get("groups", []):
            for negative in entry.get("values", []):
                if len(negative.split()) > 7:
                    ignored.append({"negative": negative, "issue": "больше 7 слов",
                                    "level": entry.get("id")})
                for keyword in keywords:
                    if fully_overlaps_keyword(negative, keyword):
                        ignored.append({"negative": negative, "keyword": keyword,
                                        "issue": "минус полностью пересекается с фразой и игнорируется",
                                        "level": entry.get("id")})
                    elif negative_blocks(negative, keyword)["blocks"]:
                        conflicts.append({"negative": negative, "keyword": keyword,
                                          "issue": "минус блокирует целевую фразу", "level": entry.get("id")})
        for item in ignored[:10]:
            self.finding(
                rule="NEG-03", category="negatives",
                title=f"Минус-фраза не работает: «{item['negative']}»",
                obj={"type": "negative", "id": item["negative"]},
                evidence=[{"source": "negatives_audit", "field": "negative", "value": item["negative"]},
                          {"source": "keywords", "field": "keyword", "value": item.get("keyword")}],
                metrics={"level": item.get("level")}, priority="P3", confidence="high",
                why=f"{item['issue']}. Показ по этой фразе продолжится.",
                next_step="Уточнить минус-фразу или добавить слово, которого нет в целевой фразе.",
            )
        if not conflicts:
            if ignored:
                return
            self.finding(
                rule="NEG-02", category="negatives", title="Конфликтов минус-фраз с фразами не найдено",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "negatives_audit", "field": "checked", "value": len(keywords)}],
                metrics={"keywords_checked": len(keywords)}, priority="P3", confidence="medium",
                why="Проверка по доступным фразам и минус-фразам не выявила пересечений, "
                    "которые отключали бы показы.",
            )
            return
        for conflict in conflicts[:10]:
            self.finding(
                rule="NEG-01", category="negatives",
                title=f"Минус-фраза «{conflict['negative']}» конфликтует с фразой",
                obj={"type": "negative", "id": conflict["negative"]},
                evidence=[{"source": "negatives_audit", "field": "negative", "value": conflict["negative"]},
                          {"source": "keywords", "field": "keyword", "value": conflict.get("keyword")}],
                metrics={"level": conflict.get("level")}, priority="P1", confidence="high",
                why=f"Проблема: {conflict['issue']}. Показ по целевой фразе может быть потерян.",
                next_step="Проверить прогноз Директа и снять или уточнить минус-фразу.",
            )

    # --- РСЯ ---------------------------------------------------------------
    def rule_placements(self):
        block = self.ctx["segments"].get("placements") or {}
        strategy = self.ctx.get("strategy") or {}
        from .extract import network_enabled
        is_network = network_enabled(strategy) or bool(block.get("rows"))
        if not block.get("available"):
            self.limit("Площадки РСЯ не анализировались: stats_placements недоступен", group="rsy")
            return
        if self.block_incomplete(block):
            self.limit("Площадки РСЯ: выгрузка неполная — кандидаты на исключение "
                       "не формируются", group="rsy")
            return
        if not is_network and not block.get("rows"):
            return
        if not block.get("rows"):
            self.limit("Площадки РСЯ: строк нет. Пустой ответ не доказывает отсутствие показов",
                       group="rsy", detail=block.get("note") or "")
            return

        campaign_cpa = self.a.get("cpa")
        threshold = self.th.get("attention_threshold_rub")
        average_ctr = self.a.get("ctr") or 0
        watch_only = 0
        for row in block["rows"]:
            cost = row.get("cost") or 0
            conversions = row.get("conversions")
            if conversions is None:
                continue
            if conversions != 0:
                continue
            if threshold and cost >= threshold:
                finding = self.finding(
                    rule="RSY-01", category="rsy_placements",
                    title=f"Площадка дороже порога внимания: {row['key']}",
                    obj={"type": "placement", "id": row["key"],
                         "path": object_path(("campaign", self.session["campaign_id"]), ("placement", row["key"]))},
                    evidence=[{"source": block["action"], "field": "Cost", "value": cost},
                              {"source": block["action"], "field": "Conversions", "value": 0},
                              {"source": "stats_compare", "field": "CPA", "value": campaign_cpa}],
                    metrics={"cost": cost, "conversions": 0, "campaign_cpa": campaign_cpa,
                             "clicks": row.get("clicks"), "ctr": row.get("ctr")},
                    threshold={"name": "2 × CPA", "value": threshold, "unit": "RUB",
                               "meaning": "порог внимания, а не основание для исключения"},
                    classification="fact",
                    priority=self.waste_priority(cost, "high"), confidence="medium",
                    why=f"Расход {fmt_money(cost)} ≥ порога внимания {fmt_money(threshold)} при 0 конверсий. "
                        "Порог показывает, куда смотреть, но не доказывает неэффективность.",
                    alternatives=["конверсии не фиксируются на площадке",
                                  "площадка работает на верх воронки",
                                  "мало кликов для вывода"],
                    next_step="Проверить клики и поведение: при малом числе кликов — наблюдать.",
                    spend={"actual": cost, "at_risk": cost, "potential_saving": None},
                )
                self.recommend(
                    finding, problem=f"Расход на площадке без конверсий: {row['key']}",
                    change="Кандидат на исключение: добавить площадку в исключённые вручную, "
                           "предварительно проверив трафик.",
                    effect={"type": "estimate", "value": cost, "unit": "RUB/период",
                            "method": "расход площадки за период A при полном исключении",
                            "conditions": ["исключение поддерживается типом кампании",
                                           "площадка не даёт конверсий в других целях"]},
                    risk={"level": "medium",
                          "notes": ["в ЕПК исключение площадок ограничено",
                                    "потеря охвата", "возможны конверсии с задержкой"]},
                    apply={"method": "manual", "tool": None,
                           "manual": "Директ → кампания → Исключённые площадки "
                                     "(текущий список через API не читается — автоматическая замена небезопасна)"},
                    verify=self.verify_template(["расход", "конверсии", "CPA"],
                                               direction="расход вниз, конверсии не падают",
                                               side_effects=["сокращение охвата"]),
                )
            else:
                watch_only += 1
                finding = self.finding(
                    rule="RSY-02", category="rsy_placements",
                    title=f"Площадка без конверсий ниже порога: {row['key']}",
                    obj={"type": "placement", "id": row["key"],
                         "path": object_path(("campaign", self.session["campaign_id"]), ("placement", row["key"]))},
                    evidence=[{"source": block["action"], "field": "Cost", "value": cost},
                              {"source": block["action"], "field": "Conversions", "value": 0},
                              {"source": "threshold", "field": "2x_cpa", "value": threshold}],
                    metrics={"cost": cost, "conversions": 0, "threshold": threshold,
                             "clicks": row.get("clicks")},
                    threshold={"name": "2 × CPA", "value": threshold, "unit": "RUB",
                               "meaning": "порог внимания: ниже порога исключение не обосновано"},
                    classification="fact", priority="P3", confidence="low",
                    why=f"Расход {fmt_money(cost)} ниже порога {fmt_money(threshold)}: исключение по порогу не обосновано. "
                        "Площадка остаётся под наблюдением.",
                    alternatives=["мало данных", "конверсии придут позже"],
                    next_step="Наблюдать; вернуться к вопросу при расходе выше порога.",
                    spend={"actual": cost, "at_risk": None, "potential_saving": None},
                )
                self.limit(f"Площадка {row['key']} ниже порога — только наблюдение", group="rsy")
                if row.get("clicks") and row.get("clicks") >= 20 and average_ctr and (row.get("ctr") or 0) > average_ctr * 3:
                    self.hypothesis(
                        title=f"Аномальный CTR площадки {row['key']}",
                        statement="CTR значительно выше среднего — возможен случайный или низкокачественный трафик.",
                        basis=[f"CTR площадки {row.get('ctr'):.2f}%", f"CTR кампании {average_ctr:.2f}%",
                               f"кликов {row.get('clicks')}"],
                        alternatives=["креатив хорошо подходит площадке", "случайные клики"],
                        check="Посмотреть поведение визитов и глубину просмотра в Метрике.",
                        source_finding=finding["id"],
                    )
        if watch_only:
            self.limit(f"Площадок ниже порога без конверсий: {watch_only} — наблюдение, не исключение",
                       group="rsy")

    # --- воронка -----------------------------------------------------------
    def rule_funnel(self):
        metrika = self.ctx["segments"].get("metrika") or {}
        rows = metrika.get("rows") or []
        clicks = self.a.get("clicks")
        conversions = self.a.get("conversions")
        cost = self.a.get("cost")
        stages = {
            "impressions": self.a.get("impressions"),
            "clicks": clicks,
            "visits": (rows[0].get("visits") if rows else None),
            "leads": conversions,
            "qualified_leads": None,
            "sales": None,
        }
        missing = [name for name, value in stages.items() if value is None]
        self.ctx["funnel"] = {"stages": stages, "missing": missing}
        if missing:
            self.limit("Воронка неполная: нет этапов " + ", ".join(missing), group="funnel")
        if stages["visits"] is not None and conversions is not None and clicks:
            visits = stages["visits"]
            conv_per_visit = conversions / visits * 100 if visits else None
            self.finding(
                rule="FUN-01", category="funnel", title="Конверсия по этапам воронки",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "stats_compare", "field": "Клики", "value": clicks},
                          {"source": "metrika_direct_cpa", "field": "Visits", "value": visits},
                          {"source": "stats_compare", "field": "Конверсии", "value": conversions}],
                metrics={"clicks": clicks, "visits": visits, "leads": conversions,
                         "cr_click_pct": (conversions / clicks * 100) if clicks else None,
                         "cr_visit_pct": conv_per_visit, "cost": cost},
                priority="P2", confidence="medium",
                why=f"Из {clicks} кликов визитов {visits}, заявок {conversions}. "
                    "Разница между кликами и визитами и между визитами и заявками — разные потери.",
                next_step="Разделить потери: доставка на сайт и конверсия сайта.",
            )
        eligible = [
            {"id": stable_id("EX", "funnel", method)[:24], "name": method,
             "metric": "CPA", "unit": "RUB",
             "value": None if self.a.get("cost") is None or stages["leads"] is None
             else (self.a["cost"] / stages["leads"] if stages["leads"] else None)}
            for method in ("заявка", "квалифицированный лид", "продажа")
        ]
        # Экономика: только если есть выручка или стоимость продажи, заданные пользователем.
        economics = self.session.get("economics") or {}
        if economics.get("revenue_rub") and cost:
            drr = cost / economics["revenue_rub"] * 100
            self.finding(
                rule="FUN-02", category="funnel", title="ДРР по подтверждённой выручке",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "session.economics", "field": "revenue_rub",
                           "value": economics["revenue_rub"]},
                          {"source": "stats_compare", "field": "Расход", "value": cost}],
                metrics={"cost": cost, "revenue": economics["revenue_rub"], "drr_pct": drr,
                         "roas": economics["revenue_rub"] / cost if cost else None},
                priority="P2", confidence="high",
                why=f"ДРР {drr:.1f}% при выручке {fmt_money(economics['revenue_rub'])}. "
                    "Источник выручки — выгрузка пользователя, не оценка.",
                next_step="Сверить выручку с бухгалтерией за тот же период.",
            )
        elif conversions:
            self.limit("Прибыль и ДРР не считались: нет подтверждённой выручки или стоимости продажи",
                       group="funnel")
        self.ctx["funnel"]["economics"] = economics or None
        self.ctx["funnel"]["eligibility"] = eligible

    # --- рост --------------------------------------------------------------
    def rule_growth(self):
        groups = self.ctx["segments"].get("adgroups") or {}
        campaign_cpa = self.a.get("cpa")
        total_cost = self.a.get("cost")
        if groups.get("rows") and campaign_cpa:
            for row in groups["rows"]:
                conv = row.get("conversions") or 0
                cpa = row.get("cpa")
                if conv >= max(3, (self.th.get("min_conversions") or 10) // 3) and cpa and cpa < campaign_cpa * 0.8:
                    share = (row.get("cost") or 0) / total_cost * 100 if total_cost else None
                    self.growth.append({
                        "id": stable_id("G", "adgroup", row.get("key"))[:40],
                        "type": "effective_group",
                        "object": {"type": "adgroup", "id": row.get("key")},
                        "evidence": [{"source": "stats_adgroups", "field": "CPA", "value": cpa},
                                     {"source": "stats_adgroups", "field": "Conversions", "value": conv},
                                     {"source": "stats_compare", "field": "CPA", "value": campaign_cpa}],
                        "why": f"Группа даёт CPA {fmt_money(cpa)} при {conv} конверсиях — лучше среднего.",
                        "confidence": "medium",
                        "action": "Проверить, можно ли усилить группу без роста CPA: тексты, расширения, "
                                  "дополнительные фразы того же интента.",
                        "promise": "дополнительные заявки не гарантируются",
                        "share_of_cost_pct": share,
                    })
        if not self.growth:
            self.limit("Возможности роста не найдены: нет сегментов с устойчиво низким CPA",
                       group="growth")
        audiences = self.ctx["segments"].get("audiences") or {}
        if audiences.get("rows"):
            self.limit("Аудитории: срез собран, отдельных правил для аудиторий в этой версии нет",
                       group="growth")

    # --- история -----------------------------------------------------------
    def rule_history(self):
        history = self.ctx.get("history") or {}
        if not history.get("available"):
            self.limit("История изменений недоступна: журнал и changes_check не получены", group="history")
            return
        period = self.ctx["periods"]["a"]
        changes = history.get("changes") or []
        in_period = []
        for change in changes:
            at = str(change.get("at") or "")
            if period.get("from") and at[:10] >= period["from"] and at[:10] <= (period.get("to") or ""):
                in_period.append(change)
        if in_period:
            self.finding(
                rule="HIS-01", category="history", title="В периоде A были изменения кампании",
                obj={"type": "campaign", "id": self.session["campaign_id"]},
                evidence=[{"source": "campaign_journal", "field": "changes", "value": in_period[:5]}],
                metrics={"changes_in_period": len(in_period),
                         "changes_total": len(changes)},
                classification="fact", priority="P2", confidence="high",
                why="Изменения и динамика совпали по времени. Это последовательность событий, "
                    "а не доказанная причинная связь.",
                alternatives=["изменение не повлияло на результат", "одновременно изменился спрос"],
                next_step="Сравнить метрики до и после каждой правки отдельно.",
            )
        for rec in list(self.recommendations):
            text = (rec.get("proposed_change") or "").lower()
            for change in changes:
                summary = str(change.get("summary") or "").lower()
                if summary and any(word in summary for word in ("минус", "ставк", "объявлен", "площад")):
                    if any(word in text for word in ("минус", "ставк", "объявлен", "площад")):
                        rec.setdefault("history", {})["similar_change"] = change
                        rec["history"]["note"] = "похожее изменение уже выполнялось — проверить результат перед повтором"
                        break

    # --- монитор -----------------------------------------------------------
    def rule_monitor(self):
        previous = self.ctx.get("previous_audit")
        if not previous:
            self.limit("Режим monitor: предыдущий снимок аудита не найден — сравнение недоступно",
                       group="monitor")
            return
        prev_metrics = (previous.get("metrics") or {}).get("a") or {}
        deltas = {}
        for key in ("impressions", "clicks", "cost", "conversions", "cpa", "ctr", "cr"):
            deltas[key] = met.compare(self.a.get(key), prev_metrics.get(key),
                                      kind="rate" if key in ("ctr", "cr") else "count")
        prev_findings = {f.get("rule_id") for f in previous.get("findings") or []}
        current_rules = {f["rule_id"] for f in self.findings}
        self.ctx["monitor"] = {
            "available": True,
            "previous_audit_id": previous.get("audit_id"),
            "previous_created_at": previous.get("created_at"),
            "metric_deltas": deltas,
            "new_rules": sorted(current_rules - prev_findings),
            "resolved_rules": sorted(prev_findings - current_rules),
            "persisting_rules": sorted(current_rules & prev_findings),
        }
        self.finding(
            rule="MON-01", category="monitor", title="Сравнение с предыдущим аудитом",
            obj={"type": "campaign", "id": self.session["campaign_id"]},
            evidence=[{"source": "previous_audit", "field": "audit_id", "value": previous.get("audit_id")}],
            metrics={"cost_delta_pct": deltas["cost"].get("pct"),
                     "cpa_delta_pct": deltas["cpa"].get("pct"),
                     "new_rules": self.ctx["monitor"]["new_rules"]},
            priority="P2", confidence="medium",
            why="Сравнение строится на двух реально существующих снимках, а не на памяти агента.",
            next_step="Разобрать новые правила и проверить, выполнены ли прошлые рекомендации.",
        )

    # --- эксперименты ------------------------------------------------------
    def rule_experiments(self):
        for hypothesis in self.hypotheses:
            self.ctx.setdefault("experiments", []).append({
                "id": stable_id("EXP", hypothesis["id"])[:24],
                "hypothesis_id": hypothesis["id"],
                "statement": hypothesis["statement"],
                "metric": "CPA",
                "baseline": self.a.get("cpa"),
                "start_condition": "не менять другие настройки кампании в период теста",
                "stop_condition": "остановить при падении конверсий более чем на 30% или росте CPA более чем на 50%",
                "min_duration_days": 14,
                "sufficiency": {"min_clicks": self.th.get("min_clicks"),
                                "min_conversions": self.th.get("min_conversions")},
                "success": "CPA ниже базового при сопоставимом объёме",
                "failure": "CPA не изменился или вырос",
            })
        if not self.hypotheses:
            self.limit("Гипотезы для эксперимента не сформированы: нет находок с низкой уверенностью",
                       group="experiment")


def run(ctx: dict) -> dict:
    return Engine(ctx).run()
