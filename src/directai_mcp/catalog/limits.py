"""Unsupported API capabilities (SPEC 8, limits.py).

Checked before catalog search: on match the server returns a
'web-interface only' notice instead of action results.
"""

from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _stems(text: str) -> set[str]:
    """Same stemming as catalog search: lowercase, cut to 5 chars if longer."""
    return {w[:5] if len(w) > 4 else w for w in _TOKEN_RE.findall(text.lower())}


LIMITS: tuple[dict[str, object], ...] = (
    {
        "id": "landing-content",
        "triggers": (
            "clients.site",
            "clientssite",
            "турбо-страниц",
            "турбостраниц",
            "turbopage",
            "лендинг",
            "содержимое сайта",
        ),
        "message": (
            "Контент лендингов (clients.site) и турбо-страниц доступен "
            "только в веб-интерфейсе: API Директа не отдаёт содержимое страниц."
        ),
    },
    {
        "id": "cpm-video",
        "triggers": ("cpm-видео", "видеокреатив", "videocreative", "cpm видео"),
        "message": (
            "Создание CPM-видеокреативов доступно только в веб-интерфейсе: "
            "метод создания в API отсутствует."
        ),
    },
    {
        "id": "old-carousel",
        "triggers": ("карусель", "carousel"),
        "message": (
            "Состав каруселей у старых TEXT_AD доступен только в веб-интерфейсе: "
            "API не возвращает структуру карусели."
        ),
    },
)


def match_limit(query: str) -> str | None:
    """Return the notice text if the query hits an unsupported capability.

    Every stem of a single trigger must be present (AND within a trigger),
    so bare 'cpm' does not false-trigger the video-creative limit.
    """
    q_stems = _stems(query)
    for entry in LIMITS:
        triggers = entry["triggers"]
        assert isinstance(triggers, tuple)
        if any(_stems(t) <= q_stems for t in triggers):
            message = entry["message"]
            assert isinstance(message, str)
            return message
    return None
