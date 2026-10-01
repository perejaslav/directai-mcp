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
    dump_dir: str | None = Field(
        default=None,
        description=("v1.4.0: папка сессии dump — файл пишется как "
                     "<NN>_<action>.json с конвертом (raw + manifest), "
                     "а не в общий reports/."),
    )
    dump_tag: str | None = Field(
        default=None,
        description=("v1.4.0: суффикс имени файла в dump_dir "
                     "(напр. 'archived' для повторного вызова)."),
    )

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


_ID_KEY_RE = re.compile(r"(^|_)(id|ids)$", re.IGNORECASE)


def str_ids_display(row: dict) -> dict:
    """v1.4.0: ID-подобные int-значения строки -> str (ТЗ: все ID строками).

    Только ключи Id/Ids/*Id/*Ids (не трогает деньги, счётчики, BidModifier).
    """
    out = dict(row)
    for key, value in out.items():
        if isinstance(value, int) and not isinstance(value, bool) \
                and _ID_KEY_RE.search(str(key)):
            out[key] = str(value)
    return out


def str_ids_deep(value: object) -> object:
    """v1.4.0: рекурсивная версия str_ids_display для raw-объектов API."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            item = str_ids_deep(item)
            if isinstance(item, int) and not isinstance(item, bool) \
                    and _ID_KEY_RE.search(str(key)):
                item = str(item)
            out[key] = item
        return out
    if isinstance(value, list):
        return [str_ids_deep(item) for item in value]
    return value


def save_envelope(dump_dir: str | Path, file_name: str,
                  envelope: dict) -> tuple[Path, str]:
    """v1.4.0: детерминированный JSON-конверт (ensure_ascii=False, UTF-8)."""
    import hashlib
    import json as _json

    path = Path(dump_dir)
    path.mkdir(parents=True, exist_ok=True)
    target = path / file_name
    text = _json.dumps(envelope, ensure_ascii=False, indent=1,
                       sort_keys=True, default=str)
    target.write_text(text + "\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    return target, digest


def append_manifest(dump_dir: str | Path, entry: dict) -> tuple[Path, int]:
    """v1.4.0: дописать запись в manifest.json; вернуть (путь, seq)."""
    import json as _json

    path = Path(dump_dir)
    path.mkdir(parents=True, exist_ok=True)
    manifest = path / "manifest.json"
    items: list = []
    if manifest.exists():
        try:
            items = _json.loads(manifest.read_text(encoding="utf-8"))
            assert isinstance(items, list)
        except (ValueError, AssertionError):
            items = []
    seq = len(items) + 1
    entry = dict(entry, seq=seq)
    items.append(entry)
    manifest.write_text(
        _json.dumps(items, ensure_ascii=False, indent=1, sort_keys=True)
        + "\n",
        encoding="utf-8")
    return manifest, seq


def manifest_seq(dump_dir: str | Path) -> int:
    """v1.4.0: следующий порядковый номер в manifest.json (NN имен)."""
    import json as _json

    manifest = Path(dump_dir) / "manifest.json"
    if not manifest.exists():
        return 1
    try:
        items = _json.loads(manifest.read_text(encoding="utf-8"))
        return len(items) + 1 if isinstance(items, list) else 1
    except ValueError:
        return 1


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


async def geo_regions(ctx: Ctx, tally: dict | None = None) -> list[dict]:
    """GeoRegions dictionary with per-process cache (shared by actions)."""
    from directai_mcp.api.direct import DirectClient

    if "GeoRegions" not in _GEO_CACHE:
        client = DirectClient(token=ctx.token, sandbox=ctx.sandbox)
        try:
            result = await client.call(
                "dictionaries", "get", {"DictionaryNames": ["GeoRegions"]}, None,
                tally=tally,
            )
        finally:
            await client.aclose()
        regions = result.get("GeoRegions", [])
        _GEO_CACHE["GeoRegions"] = regions if isinstance(regions, list) else []
    elif tally is not None:
        # Кеш: ответ известен полным (Dictionaries.get — один ответ без
        # страниц), новых запросов не было.
        tally["complete"] = True
        tally.setdefault("versions", ["v5"])
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


def write_dump_sections(
    ctx: Ctx,
    dump_dir: str,
    dump_action: str,
    account: str | None,
    params_dict: dict,
    sections: dict[str, dict],
    field_names: dict,
    tally: dict | None,
    logins: list,
    scope: str | None,
    warnings: list[str],
    truncated: bool,
    dump_tag: str | None = None,
) -> str:
    """v1.4.0: конверт с произвольными секциями + manifest + describe.

    sections: {имя: {"columns": [...], "display_rows": [...],
                     "raw_items": [...]}} — ID нормализуются здесь.
    """
    import datetime
    import json as _json
    from pathlib import Path as _Path

    import directai_mcp
    from directai_mcp.catalog.registry import ACTIONS  # lazy: без цикла

    seq = manifest_seq(dump_dir)
    stem = f"{seq:02d}_{dump_action}"
    if dump_tag:
        stem += f"_{dump_tag}"
    file_name = stem + ".json"
    tally = tally or {}
    versions = tally.get("versions", [])
    net = ctx.net
    norm_sections = {}
    for sec_name, sec in sections.items():
        norm_sections[sec_name] = {
            "columns": sec.get("columns", []),
            "display_rows": [str_ids_display(r)
                             for r in sec.get("display_rows", [])],
            "raw_items": [str_ids_deep(i)
                          for i in sec.get("raw_items", [])],
        }
    envelope = {
        "envelope_version": 1,
        "action": dump_action,
        "params": params_dict,
        "account": account or "all",
        "account_login": logins[0] if len(logins) == 1 else list(logins),
        "api_version": versions[0] if len(versions) == 1 else versions,
        "requested_field_names": field_names,
        "fetched_at": datetime.datetime.now().astimezone().isoformat(
            timespec="seconds"),
        "pages_fetched": tally.get("pages", 0),
        "pagination_complete": tally.get("complete"),
        "truncated": truncated,
        "units": {
            "spent": net.units_used,
            "rests": {k: list(v) for k, v in net.rests.items()},
        },
        "warnings": list(warnings),
        "sections": norm_sections,
    }
    if not tally.get("pages") and tally.get("complete") is not True:
        envelope["warnings"].append(
            "нет данных пагинации: вызовы API не зафиксированы tally")
    path, digest = save_envelope(dump_dir, file_name, envelope)
    describe_path = _Path(dump_dir) / f"describe_{dump_action}.json"
    if not describe_path.exists():
        act = ACTIONS.get(dump_action)
        if act is not None:
            desc = {
                "action": act.name,
                "mode": act.mode,
                "summary": act.summary,
                "params_schema": act.params.model_json_schema(),
                "directai_version": directai_mcp.__version__,
            }
            describe_path.write_text(
                _json.dumps(desc, ensure_ascii=False, indent=1,
                            sort_keys=True, default=str) + "\n",
                encoding="utf-8")
    manifest, _ = append_manifest(dump_dir, {
        "action": dump_action,
        "params": params_dict,
        "file": file_name,
        "sha256": digest,
        "fetched_at": envelope["fetched_at"],
        "pagination_complete": envelope["pagination_complete"],
        "truncated": truncated,
        **({"scope": scope} if scope else {}),
    })
    return f"Dump-конверт: {path} (manifest: {manifest})."


def _dump_envelope_line(
    ctx: Ctx,
    dump_dir: str,
    dump_tag: str | None,
    dump_action: str,
    account: str | None,
    name: str,
    columns: list[str],
    rows: list[dict],
    dump_params: dict | None,
    dump_raw: dict[str, list] | None,
    dump_fields: dict | None,
    dump_tally: dict | None,
    dump_logins: list | None,
    dump_scope: str | None,
    errors: list[str] | None,
    truncated: bool,
    dump_extra: dict[str, list] | None = None,
) -> str:
    """v1.4.0: односекционный конверт (делегирует write_dump_sections)."""
    return write_dump_sections(
        ctx,
        dump_dir,
        dump_action,
        account,
        dump_params or {},
        {
            name: {"columns": columns, "display_rows": rows,
                   "raw_items": (dump_raw or {}).get(name, [])},
            **{sec: {"columns": [], "display_rows": [],
                     "raw_items": items}
                for sec, items in (dump_extra or {}).items()},
        },
        dump_fields or {},
        dump_tally,
        list(dump_logins or []),
        dump_scope,
        list(errors or []),
        truncated,
        dump_tag=dump_tag,
    )


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
    # v1.4.0: файловый конверт для dump (только при dump_dir).
    dump_dir: str | None = None,
    dump_tag: str | None = None,
    dump_action: str | None = None,
    dump_params: dict | None = None,
    dump_raw: dict[str, list] | None = None,
    dump_fields: dict | None = None,
    dump_tally: dict | None = None,
    dump_logins: list | None = None,
    dump_scope: str | None = None,
    dump_extra: dict[str, list] | None = None,
) -> str:
    """Cap rows at 200, render table, autosave full result on cut or demand.

    Шаг 1.1-3: output=file пишет полный результат в reports_dir (format
    json/md/csv) и возвращает путь + сводку (первые 20 строк); inline всегда
    несёт явный truncated-флаг. save_as — устаревший алиас file-режима.
    exports/-автосохранение при обрезке оставлено как было (устарело).

    v1.4.0: при dump_dir + dump_action вместо reports/ пишется детерминированный
    JSON-конверт <NN>_<action>[_<tag>].json (raw + manifest + describe);
    в чат — сводка и путь к конверту.
    """
    from directai_mcp.config import reports_base_dir

    if save_as:
        output, format = "file", save_as
    dump_mode = bool(dump_dir and dump_action)
    shown_cap = min(requested or ctx.settings.max_rows, MAX_TOOL_ROWS)
    shown = min(len(rows), shown_cap)
    if output == "file" and not dump_mode:
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
        if output == "file" and dump_mode:
            # v1.4.0: сводка без записи в общий reports/ (файл — конверт ниже).
            out = render_table(
                context, columns, rows, FILE_SUMMARY_ROWS,
                money_cols=money_cols, with_totals=with_totals,
                single_goal=single_goal, totals_override=totals_override,
                totals_suffix=totals_suffix,
                top_line=top_line, header_map=header_map,
                value_map=value_map, revenue_label=revenue_label,
                totals_label=totals_label,
                extra_totals_lines=extra_totals_lines,
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
    if dump_mode:
        out += "\n\n" + _dump_envelope_line(
            ctx, dump_dir or "", dump_tag, dump_action or "", account,
            name, columns, rows, dump_params, dump_raw, dump_fields,
            dump_tally, dump_logins, dump_scope, errors,
            truncated=len(rows) > FILE_SUMMARY_ROWS,
            dump_extra=dump_extra,
        )
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
