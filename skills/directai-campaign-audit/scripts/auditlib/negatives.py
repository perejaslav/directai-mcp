#!/usr/bin/env python3
"""Работа с минус-фразами и проверка их безопасности (ТЗ §4.5, §7.1).

Опора — документация Яндекса:
- минус-фраза исключает показ, только если ВСЕ её слова есть в запросе;
- если все слова минус-фразы входят в ключевую фразу, минус игнорируется;
- операторы: `"..."` — только эти слова, `[]` — фиксированный порядок,
  `!` — точная словоформа, `+` — обязательное слово;
- цену, отзывы и гео-слова нельзя минусовать автоматически.

Морфология без словаря не восстанавливается: совпадение по основе помечается
как приблизительное и требует проверки в прогнозе Директа.
"""
from __future__ import annotations

import re

OPERATORS = ('"', "[", "]", "!", "+")
WORD_RE = re.compile(r"[0-9a-zA-Zа-яА-ЯёЁ]+")
MAX_WORDS_IN_NEGATIVE = 7


def words(text: str) -> list[str]:
    return [w.lower().replace("ё", "е") for w in WORD_RE.findall(str(text or ""))]


def same_word(left: str, right: str) -> str | None:
    """'exact' | 'stem_approx' | None. Без словаря — только префиксное приближение."""
    a, b = left.lower().replace("ё", "е"), right.lower().replace("ё", "е")
    if a == b:
        return "exact"
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 5 and long.startswith(short[:5]) and abs(len(a) - len(b)) <= 3:
        return "stem_approx"
    return None


def parse_negative(negative: str) -> dict:
    text = str(negative or "").strip().lstrip("-").strip()
    return {
        "raw": text,
        "exact_phrase": '"' in text,
        "fixed_order": "[" in text or "]" in text,
        "exact_forms": [w for w in WORD_RE.findall(text) if f"!{w}" in text],
        "mandatory": [w for w in WORD_RE.findall(text) if f"+{w}" in text],
        "words": words(text),
    }


def negative_blocks(negative: str, query: str) -> dict:
    """Исключит ли минус-фраза этот запрос. Возвращает разбор причин."""
    neg = parse_negative(negative)
    query_words = words(query)
    if not neg["words"]:
        return {"blocks": False, "reason": "пустая минус-фраза", "missing": [], "match": {}}
    match, missing = {}, []
    for word in neg["words"]:
        hit = None
        for qword in query_words:
            kind = same_word(word, qword)
            if kind:
                hit = (qword, kind)
                if kind == "exact":
                    break
        if hit:
            match[word] = {"query_word": hit[0], "kind": hit[1]}
        else:
            missing.append(word)
    blocks = not missing
    reason = ("все слова минус-фразы есть в запросе" if blocks
              else "в запросе нет слов: " + ", ".join(missing))
    return {
        "blocks": blocks,
        "reason": reason,
        "missing": missing,
        "match": match,
        "approximate": any(v["kind"] == "stem_approx" for v in match.values()),
    }


def fully_overlaps_keyword(negative: str, keyword: str) -> bool:
    """Все слова минуса входят в ключевую фразу -> минус игнорируется."""
    neg_words = words(negative)
    kw_words = words(keyword)
    if not neg_words:
        return False
    return all(any(same_word(n, k) for k in kw_words) for n in neg_words)


def conflicts_with_keywords(negative: str, keywords: list[str]) -> list[dict]:
    """Какие целевые фразы перестанут показываться, если добавить минус."""
    conflicts = []
    for keyword in keywords:
        if not keyword:
            continue
        if fully_overlaps_keyword(negative, keyword):
            conflicts.append({"keyword": keyword, "kind": "ignored_full_overlap"})
            continue
        result = negative_blocks(negative, keyword)
        if result["blocks"]:
            conflicts.append({"keyword": keyword, "kind": "blocks_keyword"})
    return conflicts


def suggest_negatives(query: str, *, keywords: list[str], protected_words: list[str],
                      exclude_terms: list[str], converting_queries: list[str] | None = None,
                      max_candidates: int = 3) -> dict:
    """Кандидаты в минус-фразы ТОЛЬКО из слов самого запроса.

    Ничего не придумывается: слово, которого нет в запросе, не может исключить
    этот запрос. Каждый кандидат проверяется на:
    - защищённые коммерческие слова;
    - наличие в заданном контексте исключений;
    - фактическую блокировку запроса;
    - полное пересечение с целевой фразой (тогда минус игнорируется);
    - блокировку запросов, которые уже дают конверсии.
    """
    query_words = words(query)
    protected = {w.lower().replace("ё", "е") for w in protected_words}
    excluded = {w.lower().replace("ё", "е") for w in exclude_terms}
    converting = list(converting_queries or [])
    candidates = []
    for word in query_words:
        if len(word) < 3:
            continue
        candidate = word
        block = negative_blocks(candidate, query)
        conflicts = conflicts_with_keywords(candidate, keywords)
        ignored = [c for c in conflicts if c["kind"] == "ignored_full_overlap"]
        blocked_converting = [q for q in converting if negative_blocks(candidate, q)["blocks"]]

        if candidate in protected:
            verdict = "protected_word"
        elif not any(same_word(candidate, term) for term in excluded):
            verdict = "not_in_exclusion_context"
        elif not block["blocks"]:
            verdict = "does_not_block_query"
        elif blocked_converting:
            verdict = "blocks_converting_query"
        elif ignored and len(ignored) == len(conflicts):
            verdict = "ignored_by_keyword"
        elif len(candidate.split()) > MAX_WORDS_IN_NEGATIVE:
            verdict = "too_long"
        else:
            verdict = "ok"
        candidates.append({
            "candidate": candidate,
            "verdict": verdict,
            "blocks_query": block["blocks"],
            "block_reason": block["reason"],
            "approximate_match": block.get("approximate", False),
            "keyword_conflicts": conflicts,
            "blocked_converting_queries": blocked_converting,
        })
    usable = [c for c in candidates if c["verdict"] == "ok"]
    return {
        "query": query,
        "query_words": query_words,
        "candidates": candidates[:max_candidates],
        "usable": usable[:max_candidates],
        "reason": (None if usable else
                   "нет безопасного кандидата: слова запроса либо защищены, "
                   "либо не входят в заданный контекст исключений, "
                   "либо минус не сработает или отсечёт конверсии"),
    }


COMMERCIAL = ("купить", "заказать", "цена", "стоимость", "недорого", "под", "ключ",
              "доставка", "монтаж", "установка", "замер", "оплата", "стоимостью")
COMPARE = ("или", "лучше", "сравнение", "отзывы", "обзор", "рейтинг", "какой", "выбрать")
INFO = ("как", "что", "почему", "чем", "своими", "руками", "инструкция", "видео",
        "фото", "картинки", "скачать", "схема", "чертеж", "бесплатно", "даром")
PROBLEM = ("не", "работает", "течет", "течёт", "сломался", "ошибка", "ремонт", "починить")
NAVIGATIONAL = ("сайт", "личный", "кабинет", "официальный", "вход", "телефон", "адрес")
IRRELEVANT_DEFAULT = ("вакансии", "вакансия", "работа", "зарплата", "резюме", "трудоустройство")


def classify_intent(query: str, *, product_terms: dict | None = None) -> dict:
    """Класс запроса и основания. Информационный запрос НЕ считается мусором."""
    q = f" {str(query or '').lower().replace('ё', 'е')} "
    tokens = set(words(q))
    product_terms = product_terms or {}
    exclude_terms = [t.lower() for t in product_terms.get("exclude_terms", [])]

    def hits(seq):
        return [w for w in seq if w in tokens]

    irrelevant_hits = [t for t in exclude_terms if t in tokens] or \
                      [w for w in IRRELEVANT_DEFAULT if w in tokens]
    scores = {
        "hot_commercial": hits(COMMERCIAL),
        "compare": hits(COMPARE),
        "informational": hits(INFO),
        "problem": hits(PROBLEM),
        "navigational": hits(NAVIGATIONAL),
        "irrelevant": irrelevant_hits,
    }
    if len(irrelevant_hits) >= 1 and not scores["hot_commercial"]:
        label, reason = "irrelevant", "совпадение с контекстом исключений: " + ", ".join(irrelevant_hits)
    elif scores["hot_commercial"]:
        label, reason = "hot_commercial", "коммерческие маркеры: " + ", ".join(scores["hot_commercial"])
    elif scores["compare"]:
        label, reason = "compare", "маркеры сравнения: " + ", ".join(scores["compare"])
    elif scores["problem"]:
        label, reason = "problem", "маркеры проблемы: " + ", ".join(scores["problem"])
    elif scores["navigational"]:
        label, reason = "navigational", "навигационные маркеры: " + ", ".join(scores["navigational"])
    elif scores["informational"]:
        label, reason = "informational", "информационные маркеры: " + ", ".join(scores["informational"])
    else:
        label, reason = "unknown", "явных маркеров нет"
    return {"intent": label, "reason": reason, "markers": {k: v for k, v in scores.items() if v}}


def is_commercial_safe_word(word: str, protected: list[str]) -> bool:
    return any(same_word(word, p) for p in protected)


def sanitize_negative(text: str) -> str:
    """Убирает служебные символы, сохраняя операторы Директа."""
    cleaned = re.sub(r"[^0-9A-Za-zА-Яа-яЁё\s\"\[\]!+\-]", " ", str(text or ""))
    return re.sub(r"\s+", " ", cleaned).strip()
