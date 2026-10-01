"""Реестр неуправляемых/устаревших настроек кампаний (B2, A1).

Единая структура: {field, status, since, message}.
Расширяется добавлением записи, без правки логики.
"""

from __future__ import annotations

CAMPAIGN_SETTING_NOTICES: list[dict] = [
    {
        "field": "ENABLE_AREA_OF_INTEREST_TARGETING",
        "status": "read_only",
        "since": "2026-08-31",
        "message": (
            "Яндекс отменил настройку 31.08.2026. "
            "Поле только читается (YES/NO), не управляется: "
            "не писать, не предлагать, не трактовать YES как действующий переключатель."
        ),
    },
]

_BY_FIELD = {n["field"]: n for n in CAMPAIGN_SETTING_NOTICES}


def get_notices() -> list[dict]:
    """Весь реестр (копия)."""
    return [dict(n) for n in CAMPAIGN_SETTING_NOTICES]


def notices_for_settings(settings: list | None) -> list[dict]:
    """Пометки для списка Settings [{Option, Value}]."""
    out: list[dict] = []
    for entry in settings or []:
        if not isinstance(entry, dict):
            continue
        opt = entry.get("Option")
        if opt in _BY_FIELD:
            notice = dict(_BY_FIELD[opt])
            notice["value"] = entry.get("Value")
            out.append(notice)
    return out


def is_read_only(field: str) -> bool:
    """Поле из notices запрещено писать."""
    n = _BY_FIELD.get(field)
    return bool(n and n.get("status") == "read_only")
