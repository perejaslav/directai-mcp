"""Shared helpers for JSON v5 get-actions (step 3)."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

import directai_mcp
from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.registry import ACCOUNT_HELP, Ctx
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import (
    FILE_SUMMARY_ROWS,
    MAX_TOOL_ROWS,
    render_table,
    save_json,
    save_report_table,
    truncated_line,
)


class GetActionParams(BaseModel):
    account: str = Field(default="all", description=ACCOUNT_HELP)
    limit: int | None = None
    save_as: Literal["csv", "md"] | None = Field(
        default=None,
        description="Устарел, используйте output/format: save_as=X ≡ output=file, format=X.",
    )
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"

    @field_validator("limit")
    @classmethod
    def _limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit must be positive")
        return value


def chunk(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def clean_phrase(value: object, cut_suffix: bool = False) -> object:
    """Bare label for autotargeting; suffix cut ONLY on explicit demand.

    v1.3.4: по умолчанию фраза возвращается целиком, как в API
    (dump v2 потерял минус-слова 30 фраз из 75). Отрезание « -…» —
    только display и только при cut_suffix=true (параметр short_phrases).
    """
    if not isinstance(value, str):
        return value
    if value == "---autotargeting":
        return "Автотаргетинг"
    if cut_suffix:
        return value.split(" -", 1)[0]
    return value


def goal_label(
    gid: object, names: dict[str, str], counters: dict[str, int] | None = None
) -> str:
    """v1.1.5 п.3: 'Имя (id[, счётчик N])' — счётчик только у явно помеченных."""
    text = str(gid)
    name = (names or {}).get(text, "")
    mark = (counters or {}).get(text)
    if name and mark is not None:
        return f"{name} ({text}, счётчик {mark})"
    if name:
        return f"{name} ({text})"
    if mark is not None:
        return f"{text} (счётчик {mark})"
    return text


def micros_to_rubles(value: object) -> Decimal | None:
    """Micros (int) -> Decimal рублей; v1.1.1 без float."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(int(value)) / 1_000_000
    except (TypeError, ValueError):
        return None


def summarize(
    ids: list, results: list[dict], id_field: str = "Id"
) -> tuple[list[str], int]:
    """Per-line API batch results -> (lines, ok_count)."""
    lines: list[str] = []
    ok = 0
    for raw_id, res in zip(ids, results):
        errors = res.get("Errors") or []
        warns = res.get("Warnings") or []
        new_id = res.get(id_field)
        if errors:
            lines.append(
                f"{raw_id}: ОШИБКА "
                + "; ".join(f"{e.get('Code')}: {e.get('Message')}" for e in errors)
            )
        else:
            ok += 1
            shown = new_id if new_id is not None else raw_id
            suffix = ""
            if warns:
                suffix = (
                    " ("
                    + "; ".join(f"{w.get('Code')}: {w.get('Message')}" for w in warns)
                    + ")"
                )
            lines.append(f"{shown}: OK{suffix}")
    return lines, ok


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 запрос, 2 запроса, 5 запросов."""
    n = abs(n) % 100
    if 11 <= n <= 14:
        return many
    digit = n % 10
    if digit == 1:
        return one
    if 2 <= digit <= 4:
        return few
    return many


def net_summary(ctx: Ctx) -> str:
    """Шаг 1.1-5: мета-строка API по счётчикам ctx (пусто — нечего показать)."""
    import logging

    stats = ctx.collect_net()
    if stats.requests == 0 and stats.units_used == 0 and not stats.rests:
        return ""
    retries = stats.retries_code + stats.retries_net
    head = (
        f"API: {stats.requests} "
        f"{_plural(stats.requests, 'запрос', 'запроса', 'запросов')} "
        f"({retries} {_plural(retries, 'повтор', 'повтора', 'повторов')})"
    )
    if stats.units_used == 0 and not stats.rests:
        if stats.direct_requests > 0:
            out = head + ". Units: н/д (заголовок отсутствует)"
        else:
            out = head + ". Reports: баллы не расходуются"
    else:
        out = head + f", Units израсходовано {stats.units_used}"
        rests = stats.rests
        if len(rests) == 1:
            (login, (rest, limit)) = next(iter(rests.items()))
            out += f"; остаток: {rest}/{limit} ({login})"
        elif rests:
            worst = min(rests, key=lambda k: rests[k][0])
            out += (
                f"; остаток: мин. {rests[worst][0]}/{rests[worst][1]} "
                f"({worst}), кабинетов {len(rests)}"
            )
        else:
            out += "; остатки: н/д (заголовок отсутствует)"
        pct = ctx.settings.units_warn_pct
        low = sorted(
            login
            for login, (rest, limit) in rests.items()
            if limit and 100 * rest / limit < pct
        )
        if low:
            out += (
                f" Внимание: остаток баллов ниже {pct}%: "
                + ", ".join(low)
                + "."
            )
    if stats.polls or stats.wait_sec:
        out += f" Опросы очереди: {stats.polls}, ожидание {stats.wait_sec:.0f} с."
    logging.getLogger("directai_mcp.net").info(
        "%s (повторы: коды %d, сеть %d; остатки: %s)",
        out,
        stats.retries_code,
        stats.retries_net,
        ", ".join(f"{k}={v[0]}/{v[1]}" for k, v in sorted(stats.rests.items())) or "—",
    )
    return out


def split_request(request: tuple) -> tuple[str, str, dict, str]:
    """(service, method, body[, version]) -> 4-tuple, default json/v5."""
    if len(request) > 3:
        return request[0], request[1], request[2], request[3]
    return request[0], request[1], request[2], "v5"


# v1.1.11: защита от устаревшего процесса. Снапшот версии в момент первого
# импорта (старт сервера); тесты могут подменять для проверки варнинга.
RUNNING_VERSION: str = directai_mcp.__version__

_VERSION_RE = re.compile(r"""__version__\s*=\s*["']([^"']+)["']""")


def _parse_version(text: str) -> tuple[int, ...] | None:
    try:
        return tuple(int(part) for part in text.strip().split("."))
    except (ValueError, AttributeError):
        return None


def on_disk_version() -> str | None:
    """Версия пакета на диске: свежее чтение __init__.py при каждом вызове."""
    try:
        text = (Path(__file__).resolve().parent.parent / "__init__.py").read_text(
            encoding="utf-8"
        )
    except OSError:
        return None
    match = _VERSION_RE.search(text)
    return match.group(1) if match else None


def version_footer() -> str:
    """v1.1.11: последняя строка ответов stats_*/campaigns_*."""
    return f"DirectAI v{RUNNING_VERSION}"


def staleness_warning() -> str | None:
    """v1.1.11: варнинг, если на диске версия новее запущенного процесса."""
    disk = on_disk_version()
    running = _parse_version(RUNNING_VERSION)
    target = _parse_version(disk) if disk else None
    if disk and running is not None and target is not None and target > running:
        return (
            f"⚠ Запущена устаревшая версия v{RUNNING_VERSION}, "
            f"на диске v{disk} — перезапустите харнес."
        )
    return None


_GEO_CACHE: dict[str, list[dict]] = {}


async def geo_regions(ctx: Ctx) -> list[dict]:
    """GeoRegions dictionary with per-process cache (shared by actions)."""
    from directai_mcp.api.direct import DirectClient

    if "GeoRegions" not in _GEO_CACHE:
        client = DirectClient(token=ctx.token, sandbox=ctx.sandbox)
        try:
            result = await client.call(
                "dictionaries", "get", {"DictionaryNames": ["GeoRegions"]}, None
            )
        finally:
            await client.aclose()
        regions = result.get("GeoRegions", [])
        _GEO_CACHE["GeoRegions"] = regions if isinstance(regions, list) else []
    return _GEO_CACHE["GeoRegions"]


async def region_names(ctx: Ctx) -> dict[int, str]:
    """Map GeoRegionId -> GeoRegionName."""
    names: dict[int, str] = {}
    for region in await geo_regions(ctx):
        try:
            names[int(region.get("GeoRegionId"))] = str(region.get("GeoRegionName"))
        except (TypeError, ValueError):
            continue
    return names


async def map_accounts(
    ctx: Ctx,
    account_value: str,
    fn: Callable[[AccountEntry, object], Awaitable[object]],
) -> list[tuple[AccountEntry, object]]:
    """Run fn(entry, client) per account (max 3 concurrent).

    Returns [(entry, result | DirectError)]. Client closed after each account.
    """
    if account_value in ("all", "active"):
        # Шаг 1.1-2 (Q5): ленивый discover/refresh кеша кабинетов.
        from directai_mcp.catalog.accounts import ensure_cache

        for note in await ensure_cache(ctx, account_value):
            ctx.notes.append(note)
    entries = ctx.accounts(account_value)
    sem = asyncio.Semaphore(3)

    async def one(entry: AccountEntry):
        client = ctx.direct()
        try:
            async with sem:
                try:
                    return (entry, await fn(entry, client))
                except DirectError as e:
                    return (entry, e)
        finally:
            await client.aclose()

    return list(await asyncio.gather(*(one(e) for e in entries)))


def finalize(
    ctx: Ctx,
    context: str,
    name: str,
    columns: list[str],
    rows: list[dict],
    requested: int | None,
    save_as: Literal["csv", "md"] | None,
    errors: list[str] | None = None,
    money_cols: tuple[str, ...] = ("Cost", "AvgCpc", "Revenue"),
    with_totals: bool = False,
    output: str = "inline",
    format: str = "json",
    account: str | None = None,
    single_goal: bool = False,
    totals_override: dict | None = None,
    totals_suffix: str | None = None,
    top_line: str | None = None,
    with_version: bool = False,
    header_map: dict[str, str] | None = None,
    value_map: dict[str, dict[str, str]] | None = None,
    # v1.1.29: подпись агрегата Revenue (None — не выводить).
    revenue_label: str | None = "Ценность целей (условная)",
    # v1.2.1: подпись главной итоговой строки + доп. строки итогов.
    totals_label: str = "Итого",
    extra_totals_lines: list[str] | None = None,
) -> str:
    """Cap rows at 200, render table, autosave full result on cut or demand.

    Шаг 1.1-3: output=file пишет полный результат в reports_dir (format
    json/md/csv) и возвращает путь + сводку (первые 20 строк); inline всегда
    несёт явный truncated-флаг. save_as — устаревший алиас file-режима.
    exports/-автосохранение при обрезке оставлено как было (устарело).
    """
    from directai_mcp.config import reports_base_dir

    if save_as:
        output, format = "file", save_as
    shown_cap = min(requested or ctx.settings.max_rows, MAX_TOOL_ROWS)
    shown = min(len(rows), shown_cap)
    if output == "file":
        reports = reports_base_dir(ctx.settings)
        if format == "json":
            path = save_json(reports, name, account, context, columns, rows)
        else:
            path = save_report_table(
                reports, name, account, context, columns, rows, format,
                single_goal, totals_override, totals_suffix, top_line,
                header_map, value_map, revenue_label,
                totals_label, extra_totals_lines,
            )
        summary = render_table(
            context, columns, rows, FILE_SUMMARY_ROWS, money_cols, with_totals,
            single_goal, totals_override, totals_suffix, top_line,
            header_map=header_map, value_map=value_map,
            revenue_label=revenue_label,
            totals_label=totals_label,
            extra_totals_lines=extra_totals_lines,
        )
        out = (
            f"Полный результат: {path} ({len(rows)} строк, формат {format}).\n\n"
            f"{summary}"
        )
    else:
        out = render_table(
            context,
            columns,
            rows,
            shown_cap,
            money_cols=money_cols,
            with_totals=with_totals,
            single_goal=single_goal,
            totals_override=totals_override,
            totals_suffix=totals_suffix,
            top_line=top_line,
            header_map=header_map,
            value_map=value_map,
            revenue_label=revenue_label,
            totals_label=totals_label,
            extra_totals_lines=extra_totals_lines,
        )
        out += f"\n\n{truncated_line(len(rows), shown)}"
    if errors:
        out += "\n\n" + "\n".join(errors)
    net = net_summary(ctx)
    if net:
        out += "\n\n" + net
    notes = list(getattr(ctx, "notes", None) or [])
    if notes and hasattr(ctx, "notes"):
        ctx.notes.clear()
    if notes:
        out += "\n\n" + "\n".join(f"Примечание: {n}" for n in notes)
    if output == "inline" and len(rows) > shown_cap and ctx.data_dir is not None:
        # Legacy-автосохранение при обрезке (шаг 1.1-3, правка: единый
        # каталог reports_dir вместо exports/; save_as сюда не попадает —
        # он выше переключён в file-режим).
        reports = reports_base_dir(ctx.settings)
        path = save_report_table(
            reports, name, account, context, columns, rows, "csv"
        )
        out += f"\n\nПоказано {shown} из {len(rows)} строк, полный отчёт: {path}"
    if with_version:
        # v1.1.11: предупреждение о протухшем процессе — первая строка ответа.
        # Футер версии добавляет вызывающий код САМЫМ последним
        # (после своих дописок): f"{out}\n\n{version_footer()}".
        warn = staleness_warning()
        if warn:
            out = f"{warn}\n\n{out}"
    return out
