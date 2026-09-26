"""Проверка текстов объявлений и расширений по правилам Директа (v1.1.18).

Источники: ref-v5/ads/add.html (длины Title/Title2/Text/Href/DisplayUrlPath,
Sitelink, «узкие» символы), ref-v5/sitelinks/add.html (Title ≤30,
Description ≤60, Href ≤1024), help technical-restrictions (уточнение ≤25),
live-пробы 26.09.2026 (5002 на «·» U+00B7 в Title2, em dash «—» проходит).

API остаётся финальным арбитром: список пунктуации собран по
документации и пробам; сомнительные символы блокируем с указанием поля
и символа — дешевле, чем 5002 на середине пачки.
"""

from __future__ import annotations

import unicodedata

# «Узкие» символы по документации API (не учитываются в лимитах Title2/Text).
NARROW = frozenset('!,.;:"')

# Длины: (всего/без узких, узких макс, слово макс).
TITLE_MAX = 56
TITLE_WORD_MAX = 22
TITLE2_MAX = 30
TITLE2_NARROW_MAX = 15
TEXT_MAX = 81
TEXT_NARROW_MAX = 15
TEXT_WORD_MAX = 23
HREF_MAX = 1024
DISPLAY_MAX = 20
SITELINK_TITLE_MAX = 30
SITELINK_DESC_MAX = 60
# Набор: 1–8 ссылок — sitelinks/add (жёстко). Сумма заголовков ≤66 —
# правило показа блока (ориентир) → предупреждение, не блок.
SITELINK_SET_MAX = 8
SITELINK_TITLES_SUM = 66
CALLOUT_MAX = 25

# DisplayUrlPath по докам: буквы, цифры, -, №, /, %, #.
# Запрещены пробел, _, двойные --, //.
_DISPLAY_EXTRA = frozenset("-№/%#")

# Пунктуация, прошедшая по докам и живым пробам (— проходит,
# · U+00B7 даёт 5002). Остальные символы вне букв/цифр — блок.
_PUNCT = frozenset(
    "!?,.;:\"'«»„“\"'‚‘‛()[]{}-–—№%+*/=<>@\\|~^$€£¥§©®™°…¿¡‐‑"
    "+−×÷"
)


def _is_letter_ok(ch: str) -> bool:
    """Буквы латинского (+турецкий) и кириллического алфавитов (текст 5002)."""
    code = ord(ch)
    if "A" <= ch <= "Z" or "a" <= ch <= "z":
        return True
    if 0x00C0 <= code <= 0x00FF:  # Latin-1: à-ÿ, в т.ч. турецкие ğış
        return unicodedata.category(ch).startswith("L")
    if 0x0100 <= code <= 0x024F:  # Latin Extended-A/B: şçў? нет — latin
        return unicodedata.category(ch).startswith("L")
    if 0x0400 <= code <= 0x04FF:  # Cyrillic: рус/укр/бел/каз
        return unicodedata.category(ch).startswith("L")
    return False


def check_symbols(field: str, value: str) -> str | None:
    """Первый недопустимый символ с указанием поля и кода (для 5002)."""
    for ch in value:
        if ch == " " or ch == "\u00A0" or ch == "#":
            continue
        if "0" <= ch <= "9":
            continue
        if _is_letter_ok(ch):
            continue
        if ch in _PUNCT:
            continue
        return f"{field}: недопустимый символ «{ch}» (U+{ord(ch):04X})"
    return None


def _effective(text: str) -> str:
    """Длина без символов шаблона # (по докам # не учитываются)."""
    return text.replace("#", "")


def counts(value: str) -> tuple[int, int]:
    """Пара (длина без «узких», число «узких») для счётчиков «N/лимит»."""
    text = _effective(value or "")
    narrow = sum(1 for ch in text if ch in NARROW)
    return len(text) - narrow, narrow


# Склейка Title/Title2 при конвертации в комбинаторное (миграция ЕПК,
# варнинг 10254 живьём): «T. T2», если сумма укладывается в 56.
# Подтверждено доками ads/add (лимиты полей) + живым 10254; учитываются
# ли «узкие» в сумме — доки молчат, считаем сырую длину без # (строго).
TITLE_SUM_MAX = 56
TITLE_GLUE = 2


def pair_sum(first: str | None, second: str | None) -> tuple[int, int, int]:
    """(len1, len2, сумма с разделителем) для пары заголовков."""
    len1 = len(_effective(first or ""))
    len2 = len(_effective(second or ""))
    return len1, len2, len1 + len2 + TITLE_GLUE


def check_title_sum(label: str, first: str | None,
                    second: str | None) -> str | None:
    """Сумма пары заголовков ≤56, иначе Title2 будет отброшен API (10254)."""
    if not second:
        return None
    len1, len2, total = pair_sum(first, second)
    if total > TITLE_SUM_MAX:
        over = total - TITLE_SUM_MAX
        return (
            f"{label}: З1 {len1} + З2 {len2} + 2 = {total} / "
            f"{TITLE_SUM_MAX}, лишних {over}"
        )
    return None


def check_length(
    field: str, value: str, base_max: int, narrow_max: int, word_max: int
) -> str | None:
    """Лимиты с учётом «узких» + длина слова."""
    text = _effective(value)
    narrow = sum(1 for ch in text if ch in NARROW)
    if len(text) - narrow > base_max:
        return f"{field}: длина {len(text) - narrow} > {base_max} без «узких»"
    if narrow > narrow_max:
        return f"{field}: «узких» знаков {narrow} > {narrow_max}"
    for word in text.split():
        if len(word) > word_max:
            return f"{field}: слово «{word[:20]}…» длиннее {word_max}"
    return None


def check_title(field: str, value: str) -> str | None:
    """Заголовок/заголовки: всего ≤56 (узкие учитываются), слово ≤22."""
    text = _effective(value)
    if len(text) > TITLE_MAX:
        return f"{field}: длина {len(text)} > {TITLE_MAX}"
    for word in text.split():
        if len(word) > TITLE_WORD_MAX:
            return f"{field}: слово «{word[:20]}…» длиннее {TITLE_WORD_MAX}"
    return check_symbols(field, value)


def check_title2(field: str, value: str) -> str | None:
    return check_length(field, value, TITLE2_MAX, TITLE2_NARROW_MAX,
                        TITLE_WORD_MAX) or check_symbols(field, value)


def check_text(field: str, value: str) -> str | None:
    return check_length(field, value, TEXT_MAX, TEXT_NARROW_MAX,
                        TEXT_WORD_MAX) or check_symbols(field, value)


def check_href(field: str, value: str) -> str | None:
    if len(value) > HREF_MAX:
        return f"{field}: длина {len(value)} > {HREF_MAX}"
    if "://" not in value:
        return f"{field}: нужен протокол и домен (https://…)"
    return None


def check_display(field: str, value: str) -> str | None:
    text = _effective(value)
    if len(text) > DISPLAY_MAX:
        return f"{field}: длина {len(text)} > {DISPLAY_MAX}"
    for ch in value:
        if ch == "#":
            continue
        if "0" <= ch <= "9" or _is_letter_ok(ch) or ch in _DISPLAY_EXTRA:
            continue
        return f"{field}: недопустимый символ «{ch}» (U+{ord(ch):04X})"
    if "--" in value:
        return f"{field}: запрещены двойные --"
    if "//" in value:
        return f"{field}: запрещены двойные //"
    return None


def check_text_ad(
    title: str,
    title2: str | None,
    text: str,
    href: str,
    display: str | None,
) -> list[str]:
    """Все поля TEXT_AD; пустые опциональные пропускаются."""
    errors = [
        e for e in (
            check_title("title", title),
            check_title2("title2", title2) if title2 else None,
            check_text("text", text),
            check_href("href", href),
            check_display("display_url_path", display) if display else None,
        )
        if e
    ]
    return errors


def check_responsive(
    titles: list[str],
    texts: list[str],
    href: str,
    display: str | None,
) -> list[str]:
    errors = []
    for i, title in enumerate(titles):
        err = check_title(f"titles[{i}]", title)
        if err:
            errors.append(err)
    for i, text in enumerate(texts):
        err = check_text(f"texts[{i}]", text)
        if err:
            errors.append(err)
    for err in (
        check_href("href", href),
        check_display("display_url_path", display) if display else None,
    ):
        if err:
            errors.append(err)
    return errors


def check_sitelink(title: str, href: str | None, description: str | None) -> list[str]:
    errors = []
    if len(_effective(title)) > SITELINK_TITLE_MAX:
        errors.append(
            f"sitelink title: длина {len(_effective(title))} > {SITELINK_TITLE_MAX}"
        )
    err = check_symbols("sitelink title", title)
    if err:
        errors.append(err)
    if href:
        err = check_href("sitelink href", href)
        if err:
            errors.append(err)
    if description:
        if len(_effective(description)) > SITELINK_DESC_MAX:
            errors.append(
                f"sitelink description: длина {len(_effective(description))}"
                f" > {SITELINK_DESC_MAX}"
            )
        err = check_symbols("sitelink description", description)
        if err:
            errors.append(err)
    return errors


def check_callout(text: str) -> str | None:
    if len(_effective(text)) > CALLOUT_MAX:
        return f"callout: длина {len(_effective(text))} > {CALLOUT_MAX}"
    return check_symbols("callout", text)


def check_sitelink_set(titles: list[str]) -> str | None:
    """Жёсткий лимит набора: 1–8 ссылок (sitelinks/add)."""
    if not 1 <= len(titles) <= SITELINK_SET_MAX:
        return (
            f"sitelink set: {len(titles)} ссылок "
            f"(лимит 1–{SITELINK_SET_MAX})"
        )
    return None


def sitelink_titles_warning(titles: list[str]) -> str | None:
    """Сумма заголовков блока ≤66 — ориентир показа, предупреждение."""
    total = sum(len(_effective(t)) for t in titles)
    if total > SITELINK_TITLES_SUM:
        return (
            f"сумма заголовков {total} > {SITELINK_TITLES_SUM}: "
            "блок может не показаться"
        )
    return None


def norm_callout(text: str) -> str:
    """Нормализованный текст уточнения для дедупа."""
    return " ".join(str(text or "").split()).casefold()
