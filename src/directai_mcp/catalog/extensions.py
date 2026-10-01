"""Read extensions_list + extensions_create (Sitelinks/AdExtensions/AdImages)."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import (
    GetActionParams,
    map_accounts,
    split_request,
    summarize,
)
from directai_mcp.catalog.registry import Ctx, action, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import MAX_TOOL_ROWS


class ExtensionsListParams(GetActionParams):
    ad_ids: list[int] = Field(default_factory=list)
    sitelink_set_ids: list[int] = Field(default_factory=list)
    extension_ids: list[int] = Field(default_factory=list)
    image_hashes: list[str] = Field(default_factory=list)


async def _section(
    ctx: Ctx,
    base: str,
    context: str,
    columns: list[str],
    rows: list[dict],
    params: ExtensionsListParams,
    errors: list[str],
) -> tuple[str, str | None]:
    """Одна секция: inline-таблица с truncated-флагом или файл (шаг 1.1-3)."""
    from directai_mcp.config import reports_base_dir
    from directai_mcp.fmt import (
        FILE_SUMMARY_ROWS,
        render_table,
        save_json,
        save_report_table,
        truncated_line,
    )

    output, format = params.output, params.format
    if params.save_as:
        output, format = "file", params.save_as
    shown_cap = min(params.limit or ctx.settings.max_rows, MAX_TOOL_ROWS)
    shown = min(len(rows), shown_cap)
    file_path: str | None = None
    if output == "file":
        reports = reports_base_dir(ctx.settings)
        if format == "json":
            file_path = str(
                save_json(reports, base, params.account, context, columns, rows)
            )
        else:
            file_path = str(
                save_report_table(
                    reports, base, params.account, context, columns, rows, format
                )
            )
        summary = render_table(
            context, columns, rows, FILE_SUMMARY_ROWS, with_totals=False
        )
        out = (
            f"Полный результат: {file_path} ({len(rows)} строк, "
            f"формат {format}).\n\n{summary}"
        )
    else:
        out = render_table(context, columns, rows, shown_cap, with_totals=False)
        out += f"\n\n{truncated_line(len(rows), shown)}"
    if errors:
        out += "\n\n" + "\n".join(errors)
    from directai_mcp.catalog.common import net_summary

    net = net_summary(ctx)
    if net:
        out += "\n\n" + net
    return out, file_path


@action(
    "extensions_list",
    "read",
    "Расширения: быстрые ссылки, уточнения, изображения",
    (
        "расширения",
        "extensions",
        "быстрые ссылки",
        "уточнения",
        "sitelinks",
        "callouts",
        "изображения",
        "display url",
        "отображаемая ссылка",
        "image",
    ),
    ExtensionsListParams,
)
async def _list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, ExtensionsListParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}

    async def fetch(entry: AccountEntry, client):
        set_ids = list(params.sitelink_set_ids)
        ext_ids = list(params.extension_ids)
        hashes = list(params.image_hashes)
        if params.ad_ids:
            ads = await client.get_all(
                "ads",
                {
                    "SelectionCriteria": {"Ids": params.ad_ids},
                    "FieldNames": ["Id"],
                    "TextAdFieldNames": [
                        "SitelinkSetId",
                        "AdImageHash",
                        "AdExtensions",
                    ],
                    "ResponsiveAdFieldNames": ["SitelinkSetId", "AdExtensions"],
                },
                entry.login,
                "Ads",
                tally=tally,
            )
            for ad in ads:
                for key in ("TextAd", "ResponsiveAd"):
                    sub = ad.get(key) or {}
                    if sub.get("SitelinkSetId") is not None:
                        set_ids.append(sub["SitelinkSetId"])
                    for e in sub.get("AdExtensions") or []:
                        if isinstance(e, dict) and e.get("AdExtensionId") is not None:
                            ext_ids.append(e["AdExtensionId"])
                    if sub.get("AdImageHash"):
                        hashes.append(sub["AdImageHash"])
        sitelink_sets = await client.get_all(
            "sitelinks",
            ({"SelectionCriteria": {"Ids": sorted(set(set_ids))}} if set_ids else {})
            | {"FieldNames": ["Id", "Sitelinks"]},
            entry.login,
            "SitelinksSets",
            tally=tally,
        )
        extensions = await client.get_all(
            "adextensions",
            (
                {"SelectionCriteria": {"Ids": sorted(set(ext_ids))}}
                if ext_ids
                else {"SelectionCriteria": {}}
            )
            | {
                "FieldNames": ["Id", "Type", "Status", "Associated"],
                "CalloutFieldNames": ["CalloutText"],
            },
            entry.login,
            "AdExtensions",
            tally=tally,
        )
        images: list[dict] = []
        if hashes or params.ad_ids or params.image_hashes:
            images = await client.get_all(
                "adimages",
                (
                    {"SelectionCriteria": {"AdImageHashes": sorted(set(hashes))}}
                    if hashes
                    else {}
                )
                | {"FieldNames": ["AdImageHash", "Name", "Type", "Associated"]},
                entry.login,
                "AdImages",
                tally=tally,
            )
        return sitelink_sets, extensions, images

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    errors: list[str] = []
    site_rows: list[dict] = []
    ext_rows: list[dict] = []
    img_rows: list[dict] = []
    raw_sets: list[dict] = []
    raw_exts: list[dict] = []
    raw_imgs: list[dict] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        sets, exts, images = payload
        raw_sets.extend(sets)
        raw_exts.extend(exts)
        raw_imgs.extend(images)
        for s in sets:
            for link in s.get("Sitelinks") or []:
                row = {
                    "SetId": s.get("Id"),
                    "Title": link.get("Title"),
                    "Href": link.get("Href") or "—",
                    "Description": link.get("Description") or "—",
                }
                if len(entries) > 1:
                    row["_account"] = entry.login
                site_rows.append(row)
        for e in exts:
            row = {
                "Id": e.get("Id"),
                "Type": e.get("Type"),
                "Status": e.get("Status"),
                "Text": (e.get("Callout") or {}).get("CalloutText", "—"),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            ext_rows.append(row)
        for img in images:
            row = {
                "Hash": img.get("AdImageHash"),
                "Name": img.get("Name"),
                "Type": img.get("Type"),
                "Associated": img.get("Associated"),
            }
            if len(entries) > 1:
                row["_account"] = entry.login
            img_rows.append(row)
    acc = ["_account"] if len(entries) > 1 else []
    accounts = ", ".join(e.login for e in entries)
    parts = [f"{mark}extensions_list: {accounts}."]
    for title, base, cols, rows in (
        (
            "Быстрые ссылки",
            "extensions_sitelinks",
            acc + ["SetId", "Title", "Href", "Description"],
            site_rows,
        ),
        (
            "Уточнения",
            "extensions_callouts",
            acc + ["Id", "Type", "Status", "Text"],
            ext_rows,
        ),
        (
            "Изображения",
            "extensions_images",
            acc + ["Hash", "Name", "Type", "Associated"],
            img_rows,
        ),
    ):
        section, _ = await _section(ctx, base, f"{title}.", cols, rows, params, [])
        parts.append(f"## {title}\n\n{section}")
    if errors:
        parts.append("\n".join(errors))
    if params.dump_dir:
        from directai_mcp.catalog.common import write_dump_sections

        scoped = bool(params.ad_ids or params.image_hashes
                      or params.sitelink_set_ids or params.extension_ids)
        parts.append(write_dump_sections(
            ctx,
            params.dump_dir,
            "extensions_list",
            params.account,
            params.model_dump(),
            {
                "extensions_sitelinks": {
                    "columns": acc + ["SetId", "Title", "Href",
                                      "Description"],
                    "display_rows": site_rows,
                    "raw_items": [
                        dict(s, linked_to_campaign=scoped)
                        for s in raw_sets],
                },
                "extensions_callouts": {
                    "columns": acc + ["Id", "Type", "Status", "Text"],
                    "display_rows": ext_rows,
                    "raw_items": [
                        dict(e, linked_to_campaign=scoped)
                        for e in raw_exts],
                },
                "extensions_images": {
                    "columns": acc + ["Hash", "Name", "Type", "Associated"],
                    "display_rows": img_rows,
                    "raw_items": [
                        dict(i, linked_to_campaign=scoped)
                        for i in raw_imgs],
                },
            },
            {"Ads": {"FieldNames": ["Id"],
                      "TextAdFieldNames": [
                          "SitelinkSetId", "AdImageHash", "AdExtensions"],
                      "ResponsiveAdFieldNames": [
                          "SitelinkSetId", "AdExtensions"]},
             "SitelinksSets": {"FieldNames": ["Id", "Sitelinks"]},
             "AdExtensions": {
                 "FieldNames": ["Id", "Type", "Status", "Associated"],
                 "CalloutFieldNames": ["CalloutText"]},
             "AdImages": {"FieldNames": ["AdImageHash", "Name", "Type",
                                         "Associated"]}},
            tally,
            [e.login for e in entries],
            "campaign" if scoped else "cabinet",
            list(errors),
            len(site_rows) > 20 or len(ext_rows) > 20
            or len(img_rows) > 20,
            dump_tag=params.dump_tag,
        ))
    return "\n\n".join(parts)


class SitelinkCreate(BaseModel):
    title: str
    href: str | None = None
    description: str | None = None


class ImageCreate(BaseModel):
    path: str
    name: str | None = None


class ExtensionsCreateParams(GetActionParams):
    sitelinks: list[SitelinkCreate] = Field(default_factory=list)
    callouts: list[str] = Field(default_factory=list)
    images: list[ImageCreate] = Field(default_factory=list)


def _read_image_b64(path: str) -> str:
    import base64
    from pathlib import Path

    data = Path(path).read_bytes()
    if len(data) > 15 * 1024 * 1024:
        raise ValueError(f"файл {path}: больше 15 МБ.")
    return base64.b64encode(data).decode("ascii")


async def _prepare_extensions_create(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    from directai_mcp.safety import adtext as _adtext

    assert isinstance(params, ExtensionsCreateParams)
    if not params.sitelinks and not params.callouts and not params.images:
        raise ValueError("укажите sitelinks, callouts или images.")
    # v1.1.18: тексты и URL по правилам Директа (до apply, вместо 5002).
    for link in params.sitelinks:
        errors = _adtext.check_sitelink(
            link.title, link.href, link.description
        )
        if errors:
            raise ValueError(
                f"быстрая ссылка «{link.title}»: " + "; ".join(errors)
            )
    # v1.1.36: лимит набора 1–8 (sitelinks/add) — блок; сумма ≤66 — варнинг.
    if params.sitelinks:
        err = _adtext.check_sitelink_set([link.title for link in params.sitelinks])
        if err:
            raise ValueError(err)
    for text in params.callouts:
        err = _adtext.check_callout(text)
        if err:
            raise ValueError(f"уточнение «{text}»: {err}")
    requests: list[tuple[str, str, dict]] = []
    preview: list[str] = []
    warnings: list[str] = []
    if params.sitelinks:
        warn = _adtext.sitelink_titles_warning(
            [link.title for link in params.sitelinks])
        if warn:
            warnings.append(f"набор быстрых ссылок: {warn}.")
    reused: dict[str, int] = {}
    fresh_callouts: list[str] = []
    if params.callouts:
        # v1.1.36: дедуп с уточнениями кабинета — совпавшие переиспользуем.
        existing = await _existing_callouts(ctx, entry)
        seen: set[str] = set()
        for text in params.callouts:
            key = _adtext.norm_callout(text)
            if key in seen:
                continue
            seen.add(key)
            if key in existing:
                reused[text] = existing[key]
            else:
                fresh_callouts.append(text)
                existing[key] = -1  # дубль внутри пачки — один раз
        if reused:
            preview.append("уточнения переиспользованы: " + ", ".join(
                f"«{t}» (Id {i})" for t, i in reused.items()))
        if not fresh_callouts and not params.sitelinks and not params.images:
            raise ValueError(
                "нечего создавать: все уточнения уже есть в кабинете "
                "(" + ", ".join(f"«{t}» Id {i}"
                                for t, i in reused.items()) + ").")
    if params.sitelinks:
        for link in params.sitelinks:
            if not link.href:
                raise ValueError(f"быстрая ссылка «{link.title}»: нужен href.")
        requests.append(
            (
                "sitelinks",
                "add",
                {
                    "SitelinksSets": [
                        {
                            "Sitelinks": [
                                {
                                    "Title": link.title,
                                    "Href": link.href,
                                    **(
                                        {"Description": link.description}
                                        if link.description
                                        else {}
                                    ),
                                }
                                for link in params.sitelinks
                            ]
                        }
                    ]
                },
            )
        )
        preview.append(f"набор быстрых ссылок: {len(params.sitelinks)} шт")
    if params.callouts and fresh_callouts:
        requests.append(
            (
                "adextensions",
                "add",
                {
                    "AdExtensions": [
                        {"Callout": {"CalloutText": text}}
                        for text in fresh_callouts
                    ]
                },
            )
        )
        preview.append(f"уточнения новые: {len(fresh_callouts)} шт")
    if params.images:
        payload = []
        for img in params.images:
            try:
                b64 = await asyncio.to_thread(_read_image_b64, img.path)
            except OSError as e:
                raise ValueError(f"файл {img.path}: {e}.") from e
            payload.append({"ImageData": b64, "Name": img.name or img.path})
        requests.append(("adimages", "add", {"AdImages": payload}))
        preview.append(f"изображения: {len(params.images)} шт")
    return {
        "before": {"reused_callouts": reused},
        "requests": requests,
        "preview": "Будет выполнено:\n" + "\n".join(f"- {line}" for line in preview),
        "warnings": warnings,
    }


async def _existing_callouts(ctx: Ctx, entry: AccountEntry) -> dict[str, int]:
    """Уточнения кабинета {норм. текст: Id} (v1.1.36, для дедупа)."""
    from directai_mcp.safety import adtext as _adtext

    client = ctx.direct()
    try:
        items = await client.get_all(
            "adextensions",
            {
                "SelectionCriteria": {},
                "FieldNames": ["Id", "Type"],
                "CalloutFieldNames": ["CalloutText"],
            },
            entry.login,
            "AdExtensions",
        )
    finally:
        await client.aclose()
    out: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict) or item.get("Id") is None:
            continue
        text = (item.get("Callout") or {}).get("CalloutText")
        if text:
            out.setdefault(_adtext.norm_callout(str(text)), int(item["Id"]))
    return out


async def _apply_extensions_create(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    from directai_mcp.api.errors import DirectError, DirectUnverifiedError

    keys = {
        "sitelinks": "AddResults",
        "adextensions": "AddResults",
        "adimages": "AddResults",
    }
    id_fields = {"sitelinks": "Id", "adextensions": "Id", "adimages": "AdImageHash"}
    client = ctx.direct()
    all_lines: list[str] = []
    oks = totals = 0
    response: dict = {}
    try:
        for service, method, body, version in (split_request(r) for r in plan.requests):
            try:
                result = await client.call(service, method, body, entry.login, version)
            except DirectUnverifiedError:
                raise
            except DirectError as e:
                return {
                    "status": "failed",
                    "lines": all_lines + [f"Ошибка API: {e.human_message()}"],
                    "response": {"error": e.human_message()},
                }
            response[f"{service}.{method}"] = result
            count = len(
                body.get(
                    "SitelinksSets", body.get("AdExtensions", body.get("AdImages", []))
                )
            )
            ids = [str(i) for i in range(count)]
            lines, ok = summarize(
                ids, result.get(keys[service], []), id_fields[service]
            )
            all_lines += lines
            oks += ok
            totals += count
    finally:
        await client.aclose()
    status = "applied" if oks == totals else "failed" if oks == 0 else "partial"
    return {"status": status, "lines": all_lines, "response": response}


async def _verify_extensions_created(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    last = getattr(plan, "last_response", None) or {}
    resp = last.get("response") or {}
    set_ids = [
        i.get("Id")
        for i in resp.get("sitelinks.add", {}).get("AddResults", [])
        if i.get("Id") is not None
    ]
    ext_ids = [
        i.get("Id")
        for i in resp.get("adextensions.add", {}).get("AddResults", [])
        if i.get("Id") is not None
    ]
    hashes = [
        i.get("AdImageHash")
        for i in resp.get("adimages.add", {}).get("AddResults", [])
        if i.get("AdImageHash")
    ]
    if not set_ids and not ext_ids and not hashes:
        before = getattr(plan, "before", None) or {}
        if not (before.get("reused_callouts") or {}):
            return {"after": None, "ok": False,
                    "note": "read-back: ничего не создано."}
    client = ctx.direct()
    confirmed: list[str] = []
    try:
        if set_ids:
            found = await client.get_all(
                "sitelinks",
                {"SelectionCriteria": {"Ids": set_ids}, "FieldNames": ["Id"]},
                entry.login,
                "SitelinksSets",
            )
            confirmed += [f"sitelink-set:{s['Id']}" for s in found]
        if ext_ids:
            found = await client.get_all(
                "adextensions",
                {"SelectionCriteria": {"Ids": ext_ids}, "FieldNames": ["Id"]},
                entry.login,
                "AdExtensions",
            )
            confirmed += [f"extension:{e['Id']}" for e in found]
        if hashes:
            found = await client.get_all(
                "adimages",
                {
                    "SelectionCriteria": {"AdImageHashes": hashes},
                    "FieldNames": ["AdImageHash"],
                },
                entry.login,
                "AdImages",
            )
            confirmed += [f"image:{i['AdImageHash']}" for i in found]
        # v1.1.36: переиспользованные уточнения подтверждаем чтением.
        reused = (getattr(plan, "before", None) or {}).get("reused_callouts") or {}
        if reused:
            found = await client.get_all(
                "adextensions",
                {"SelectionCriteria": {"Ids": sorted(reused.values())},
                 "FieldNames": ["Id"]},
                entry.login,
                "AdExtensions",
            )
            confirmed += [f"extension-reused:{e['Id']}" for e in found]
    finally:
        await client.aclose()
    reused_n = len((getattr(plan, "before", None) or {}).get("reused_callouts") or {})
    total = len(set_ids) + len(ext_ids) + len(hashes) + reused_n
    if len(confirmed) != total:
        return {
            "after": confirmed,
            "ok": False,
            "note": f"read-back НЕ подтвердил: {total - len(confirmed)} шт.",
        }
    note = f"подтверждено read-back: создано {total}."
    if reused_n:
        note += f" (переиспользовано уточнений: {reused_n})"
    return {"after": confirmed, "ok": True, "note": note}


write_action(
    "extensions_create",
    "Создание расширений: ссылки, уточнения, изображения",
    (
        "создать расширение",
        "extensions",
        "быстрые ссылки",
        "уточнение",
        "загрузить изображение",
    ),
    ExtensionsCreateParams,
    prepare=_prepare_extensions_create,
    apply=_apply_extensions_create,
    verify=_verify_extensions_created,
)
