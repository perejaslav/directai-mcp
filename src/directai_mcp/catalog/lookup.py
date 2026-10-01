"""B1: типизированный статус результата поиска кампании (v1.6.0).

lookup_status: resolved | ambiguous | not_observed | incomplete | failed
presence: configured | statistics_only | null
proves_account_empty: всегда False при пустом ответе из-за фильтра/прав/лимитов.
"""

from __future__ import annotations

LOOKUP_STATUSES = ("resolved", "ambiguous", "not_observed", "incomplete", "failed")
PRESENCES = ("configured", "statistics_only")

# Период проверки Reports для statistics_only по умолчанию (дней).
DEFAULT_LOOKUP_PERIOD_DAYS = 90


def build_lookup(
    status: str,
    presence: str | None = None,
    proves_account_empty: bool = False,
    message: str = "",
    candidates: list[dict] | None = None,
    extra: dict | None = None,
) -> dict:
    """Собрать словарь статуса поиска (только добавление полей)."""
    if status not in LOOKUP_STATUSES:
        raise ValueError(f"unknown lookup_status '{status}'")
    if presence is not None and presence not in PRESENCES:
        raise ValueError(f"unknown presence '{presence}'")
    out: dict = {
        "lookup_status": status,
        "presence": presence,
        "proves_account_empty": bool(proves_account_empty),
        "message": message,
    }
    if candidates is not None:
        out["candidates"] = list(candidates)
    if extra:
        out.update(extra)
    return out


def resolved_configured(message: str = "объект найден в Campaigns API.") -> dict:
    return build_lookup("resolved", "configured", False, message)


def resolved_statistics_only(
    message: str = "в Campaigns API нет, в Reports есть данные (архив/удалена/чужой доступ)."
) -> dict:
    return build_lookup("resolved", "statistics_only", False, message)


def not_observed(period_text: str) -> dict:
    return build_lookup(
        "not_observed",
        None,
        False,
        f"нет ни в Campaigns API, ни в Reports за проверенный период ({period_text}). "
        "Не утверждается, что кампании не существует.",
    )


def ambiguous(candidates: list[dict]) -> dict:
    return build_lookup(
        "ambiguous",
        None,
        False,
        f"поиск дал {len(candidates)} совпадений — уточните ID.",
        candidates=candidates,
    )


def incomplete(message: str) -> dict:
    return build_lookup("incomplete", None, False, message)


def failed(message: str) -> dict:
    return build_lookup("failed", None, False, message)


def lookup_line(lookup: dict) -> str:
    """Однострочный блок статуса для текстовых ответов (рядом с версией)."""
    status = lookup.get("lookup_status", "?")
    presence = lookup.get("presence")
    msg = lookup.get("message", "")
    base = f"Статус поиска: {status}"
    if presence:
        base += f", presence={presence}"
    base += f", proves_account_empty={bool(lookup.get('proves_account_empty'))}."
    if msg:
        base += f" {msg}"
    cands = lookup.get("candidates") or []
    if cands:
        shown = ", ".join(
            f"{c.get('id')} («{c.get('name')}», {c.get('state', '?')})" for c in cands[:10]
        )
        base += f" Кандидаты: {shown}."
    return base


def empty_list_message(logins: list[str], filters: str = "") -> str:
    """Сообщение для пустого campaigns_list: пусто != объекта нет."""
    who = ", ".join(logins) if logins else "—"
    tail = f" Фильтр: {filters}." if filters else ""
    return (
        f"Список пуст (proves_account_empty=false): это не доказывает, "
        f"что кабинет пуст. Возможные причины: фильтр States/Ids,{tail} "
        f"права логина, лимиты/пагинация. Кабинеты: {who}."
    )


def guard_message(lookup: dict, campaign_id: object) -> str:
    """Понятная ошибка guard для write по не-resolved+configured."""
    status = lookup.get("lookup_status", "?")
    presence = lookup.get("presence")
    msg = lookup.get("message", "")
    extra = f" (presence={presence})" if presence else ""
    return (
        f"запись в кампанию {campaign_id} отклонена до API: "
        f"статус поиска {status}{extra}. {msg} "
        f"Разрешена запись только при resolved+configured."
    )
