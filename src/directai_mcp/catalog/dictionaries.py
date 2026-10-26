"""Read action dictionaries_get (Dictionaries.get, token-level, no Client-Login)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import finalize, geo_regions
from directai_mcp.catalog.registry import Ctx, action


class DictionariesGetParams(BaseModel):
    name: str = Field(min_length=1)
    dictionary: Literal["GeoRegions", "AudienceInterests"] = "GeoRegions"
    limit: int | None = None
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"
    save_as: Literal["csv", "md"] | None = None
    dump_dir: str | None = None
    dump_tag: str | None = None

    @classmethod
    def _check_limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit must be positive")
        return value


@action(
    "dictionaries_get",
    "read",
    (
        "Справочники: поиск региона по названию; dictionary=AudienceInterests — "
        "интересы и привычки (name='*' — все)"
    ),
    (
        "интересы",
        "привычки",
        "AudienceInterests",
        "регион",
        "dictionaries",
        "справочник",
        "гео",
        "region",
        "словарь",
        "Екатеринбург",
    ),
    DictionariesGetParams,
)
async def _get(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, DictionariesGetParams)
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    tally: dict = {}
    if params.dictionary == "AudienceInterests":
        return await _interests(ctx, params, mark, tally)
    try:
        regions = await geo_regions(ctx, tally=tally)
    except DirectError as e:
        return f"Ошибка словаря GeoRegions: {e.human_message()}"
    by_id = {r.get("GeoRegionId"): r for r in regions}
    query = params.name.strip().lower()
    matches = [r for r in regions if query in str(r.get("GeoRegionName", "")).lower()]
    matches.sort(
        key=lambda r: (
            str(r.get("GeoRegionName", "")).lower() != query,
            str(r.get("GeoRegionName", "")),
        )
    )
    rows: list[dict] = []
    for region in matches:
        parent = by_id.get(region.get("ParentId"), {})
        rows.append(
            {
                "Id": region.get("GeoRegionId"),
                "Название": region.get("GeoRegionName"),
                "Тип": region.get("GeoRegionType"),
                "Вышестоящий": parent.get("GeoRegionName") or "—",
            }
        )
    context = f"{mark}dictionaries_get: поиск «{params.name}», совпадений {len(rows)}."
    return finalize(
        ctx,
        context,
        "dictionaries_get",
        ["Id", "Название", "Тип", "Вышестоящий"],
        rows,
        params.limit,
        params.save_as,
        [],
        money_cols=(),
        output=params.output,
        format=params.format,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="dictionaries_get",
        dump_params=params.model_dump(),
        dump_raw={"dictionaries_get": [
            dict(m, linked_to_campaign=False) for m in matches]},
        dump_fields={"DictionaryNames": ["GeoRegions"]},
        dump_tally=tally,
        dump_logins=[],
        dump_scope="cabinet",
    )


async def _interests(
    ctx: Ctx, params: DictionariesGetParams, mark: str, tally: dict
) -> str:
    from directai_mcp.api.direct import DirectClient

    client = DirectClient(token=ctx.token, sandbox=ctx.sandbox)
    try:
        result = await client.call(
            "dictionaries", "get", {"DictionaryNames": ["AudienceInterests"]}, None,
            tally=tally,
        )
    except DirectError as e:
        return f"Ошибка словаря AudienceInterests: {e.human_message()}"
    finally:
        await client.aclose()
    interests = result.get("AudienceInterests") or []
    by_key = {i.get("InterestKey"): i for i in interests}
    query = params.name.strip().lower()

    def path(item: dict) -> str:
        names, seen = [], set()
        while item and item.get("InterestKey") not in seen:
            seen.add(item.get("InterestKey"))
            names.append(str(item.get("Name", "")))
            item = by_key.get(item.get("ParentId"))
        return " → ".join(reversed(names))

    rows = []
    for item in interests:
        full = path(item)
        if query != "*" and query not in full.lower():
            continue
        rows.append(
            {
                "InterestKey": item.get("InterestKey"),
                "Путь": full,
                "Тип": item.get("InterestType"),
                "Можно выбрать": item.get("IsTargetable"),
            }
        )
    rows.sort(key=lambda r: r["Путь"])
    context = (
        f"{mark}dictionaries_get: AudienceInterests, поиск «{params.name}», "
        f"совпадений {len(rows)}."
    )
    return finalize(
        ctx,
        context,
        "dictionaries_get",
        ["InterestKey", "Путь", "Тип", "Можно выбрать"],
        rows,
        params.limit,
        params.save_as,
        [],
        money_cols=(),
        output=params.output,
        format=params.format,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="dictionaries_get",
        dump_params=params.model_dump(),
        dump_raw={"dictionaries_get": [dict(i, linked_to_campaign=False) for i in interests]},
        dump_fields={"DictionaryNames": ["AudienceInterests"]},
        dump_tally=tally,
        dump_logins=[],
        dump_scope="cabinet",
    )
