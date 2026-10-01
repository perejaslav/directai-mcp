"""Read action dictionaries_get (Dictionaries.get, token-level, no Client-Login)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import finalize, geo_regions
from directai_mcp.catalog.registry import Ctx, action


class DictionariesGetParams(BaseModel):
    name: str = Field(min_length=1)
    dictionary: Literal["GeoRegions"] = "GeoRegions"
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
    "Справочники: поиск региона по названию",
    (
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
