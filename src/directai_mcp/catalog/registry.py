"""Action registry: Action, @action, search, Ctx (SPEC 6.3)."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from directai_mcp.api.direct import DirectClient, NetStats
from directai_mcp.api.reports import ReportsClient
from directai_mcp.config import AccountEntry, Settings, resolve_account

Mode = Literal["read", "write"]

_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def _stems(text: str) -> set[str]:
    out: set[str] = set()
    for word in _TOKEN_RE.findall(text.lower()):
        out.add(word[:5] if len(word) > 4 else word)
    return out


@dataclass
class Ctx:
    """Per-call context for actions."""

    settings: Settings
    token: str
    sandbox: bool = False
    data_dir: Path | None = None
    notes: list[str] = field(default_factory=list)
    net: NetStats = field(default_factory=NetStats)
    _clients: list = field(default_factory=list, repr=False)

    def accounts(self, value: str) -> list[AccountEntry]:
        from directai_mcp.config import _home_of, _managed_cache, cache_age_days

        entries = resolve_account(self.settings, value)
        if value in ("all", "active"):
            home = _home_of(self.settings)
            cache = _managed_cache(home) if home is not None else {}
            if not cache.get("logins"):
                self.notes.append(
                    "кеш кабинетов отсутствует — показан legacy-набор из конфига; "
                    "выполните accounts_discover"
                )
            else:
                age = cache_age_days(cache)
                mark = f"{age} дн. назад" if age is not None else "возраст неизвестен"
                self.notes.append(
                    f"кеш кабинетов от {cache.get('updated_at') or '?'} ({mark})"
                )
                if age is not None and age > 7:
                    self.notes.append(
                        "кеш старше 7 дней — обновите через accounts_discover"
                    )
        return entries

    def reports(self) -> ReportsClient:
        client = ReportsClient(token=self.token, sandbox=self.sandbox)
        self._clients.append(client)
        return client

    def direct(self) -> DirectClient:
        client = DirectClient(token=self.token, sandbox=self.sandbox)
        self._clients.append(client)
        return client

    def collect_net(self) -> NetStats:
        """Шаг 1.1-5: слить счётчики созданных клиентов в ctx.net (1 раз)."""
        for client in self._clients:
            self.net.merge(client.stats)
        self._clients.clear()
        return self.net


@dataclass(frozen=True)
class Action:
    name: str
    mode: Mode
    summary: str
    keywords: tuple[str, ...]
    params: type[BaseModel]
    run: Callable[[Ctx, BaseModel], Awaitable[str]] | None = None
    prepare: Any = None
    apply: Any = None
    verify: Any = None


ACTIONS: dict[str, Action] = {}


def action(
    name: str,
    mode: Mode,
    summary: str,
    keywords: tuple[str, ...],
    params: type[BaseModel],
):
    """Register a catalog read action."""

    def wrap(run: Callable[[Ctx, BaseModel], Awaitable[str]]) -> Action:
        act = Action(
            name=name,
            mode=mode,
            summary=summary,
            keywords=keywords,
            params=params,
            run=run,
        )
        ACTIONS[name] = act
        return act

    return wrap


def write_action(
    name: str,
    summary: str,
    keywords: tuple[str, ...],
    params: type[BaseModel],
    *,
    prepare,
    apply,
    verify,
) -> Action:
    """Register a catalog write action (goes through plan_write)."""
    act = Action(
        name=name,
        mode="write",
        summary=summary,
        keywords=keywords,
        params=params,
        run=None,
        prepare=prepare,
        apply=apply,
        verify=verify,
    )
    ACTIONS[name] = act
    return act


# Шаг 1.1-4 (P3): единый текст про кабинеты для описаний параметра account.
ACCOUNT_HELP = (
    "Кабинет: алиас, логин, all или active. Для аналитики по умолчанию "
    "используйте active (кабинеты с активными кампаниями); all — все "
    "кабинеты из кеша discover, дорого по баллам и времени."
)


def search(query: str, mode: str = "any") -> list[Action]:
    """Stem search over name/summary/keywords; keywords match counts ×2."""
    from directai_mcp.catalog import limits as limits_mod

    if mode not in ("read", "write", "any"):
        raise ValueError("mode must be read, write or any")
    _ = limits_mod  # checked by server before calling
    # Короткие стеммы (по/за/на/с) в скоринге не участвуют — иначе ×2 за
    # служебное слово перевешивает предметное совпадение.
    q_stems = {s for s in _stems(query) if len(s) > 2}
    scored: list[tuple[int, Action]] = []
    for act in ACTIONS.values():
        if mode != "any" and act.mode != mode:
            continue
        text_stems = _stems(act.name + " " + act.summary)
        kw_stems = _stems(" ".join(act.keywords))
        score = len(q_stems & text_stems) + 2 * len(q_stems & kw_stems)
        if query.strip().lower() == act.name:
            score += 10
        if score > 0:
            scored.append((score, act))
    scored.sort(key=lambda item: (-item[0], item[1].name))
    return [act for _, act in scored[:10]]


def categories() -> str:
    """Шаг 1.1-4: компактный список всех категорий при пустом поиске."""
    groups: dict[str, list[str]] = {}
    for name in sorted(ACTIONS):
        if name.startswith("bid_modifiers"):
            key = "bid_modifiers"
        else:
            key = name.split("_", 1)[0]
        groups.setdefault(key, []).append(name)
    lines = ["Категории действий:"]
    for prefix, names in sorted(groups.items()):
        lines.append(f"- {prefix}: {', '.join(names)}")
    return "\n".join(lines)


# Шаг 1.1-4 (Q2): темы-подсказки как данные (не хардкод в server.py).
HINTS: tuple[dict[str, object], ...] = (
    {
        "id": "output-file",
        "triggers": ("output", "файл", "выгрузка", "export", "полный результат"),
        "message": (
            "Полный результат за один вызов: добавьте output=file "
            "(формат — format=json|md|csv, по умолчанию json)."
        ),
    },
)


def match_hint(query: str) -> str | None:
    """Вернуть текст подсказки, если запрос содержит триггер темы."""
    q_stems = _stems(query)
    for entry in HINTS:
        triggers = entry["triggers"]
        assert isinstance(triggers, tuple)
        if any(_stems(t) <= q_stems for t in triggers):
            message = entry["message"]
            assert isinstance(message, str)
            return message
    return None
