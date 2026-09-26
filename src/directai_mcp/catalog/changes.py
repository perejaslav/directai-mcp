"""Read action changes_check (Changes.check)."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from directai_mcp.api.errors import DirectError
from directai_mcp.catalog.common import finalize, map_accounts
from directai_mcp.catalog.registry import ACCOUNT_HELP, Ctx, action
from directai_mcp.config import AccountEntry

CHECK_FIELDS = ["CampaignIds", "CampaignsStat", "AdGroupIds", "AdIds"]


class ChangesCheckParams(BaseModel):
    account: str = Field(default="all", description=ACCOUNT_HELP)
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"
    since: str = Field(min_length=10)
    campaign_ids: list[int] = Field(default_factory=list)
    adgroup_ids: list[int] = Field(default_factory=list)
    ad_ids: list[int] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=lambda: list(CHECK_FIELDS))
    limit: int | None = None
    save_as: Literal["csv", "md"] | None = None

    @field_validator("limit")
    @classmethod
    def _limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit must be positive")
        return value

    @field_validator("fields")
    @classmethod
    def _fields(cls, value: list[str]) -> list[str]:
        for item in value:
            if item not in CHECK_FIELDS:
                raise ValueError(f"unknown field '{item}'")
        if not value:
            raise ValueError("fields must not be empty")
        return value


def _normalize_timestamp(value: str) -> str:
    """Accept YYYY-MM-DD[ HH:MM[:SS]] or ISO with T/Z; return ISO Z."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1]
    text = text.replace("T", " ")
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})(?: (\d{2}:\d{2}(?::\d{2})?))?", text)
    if not match:
        raise ValueError("since must be YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
    time_part = match.group(2) or "00:00:00"
    if len(time_part) == 5:
        time_part += ":00"
    return f"{match.group(1)}T{time_part}Z"


@action(
    "changes_check",
    "read",
    "Что менялось с даты: кампании, группы, объявления",
    ("изменения", "changes", "что менялось", "check", "модификации"),
    ChangesCheckParams,
)
async def _check(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, ChangesCheckParams)
    scopes = [s for s in (params.campaign_ids, params.adgroup_ids, params.ad_ids) if s]
    if len(scopes) != 1:
        return "Ошибка: укажите ровно один из campaign_ids, adgroup_ids, ad_ids."
    try:
        timestamp = _normalize_timestamp(params.since)
    except ValueError as e:
        return f"Ошибка: {e}"
    mark = "[ПЕСОЧНИЦА] " if ctx.sandbox else ""
    if params.campaign_ids:
        scope = {"CampaignIds": params.campaign_ids}
    elif params.adgroup_ids:
        scope = {"AdGroupIds": params.adgroup_ids}
    else:
        scope = {"AdIds": params.ad_ids}
    body = dict(scope, FieldNames=params.fields, Timestamp=timestamp)

    async def fetch(entry: AccountEntry, client):
        return await client.call("changes", "check", dict(body), entry.login)

    results = await map_accounts(ctx, params.account, fetch)
    entries = [e for e, _ in results]
    columns = ["Статус", "Тип", "Id", "BorderDate"]
    rows: list[dict] = []
    errors: list[str] = []
    next_marks: list[str] = []
    for entry, payload in results:
        if isinstance(payload, DirectError):
            errors.append(f"⚠ {entry.login}: {payload.human_message()}")
            continue
        assert isinstance(payload, dict)
        modified = payload.get("Modified") or {}
        for item in modified.get("CampaignsStat") or []:
            rows.append(
                {
                    "_account": entry.login,
                    "Статус": "Статистика",
                    "Тип": "Campaign",
                    "Id": item.get("CampaignId"),
                    "BorderDate": item.get("BorderDate"),
                }
            )
        for key, label in (
            ("CampaignIds", "Campaign"),
            ("AdGroupIds", "AdGroup"),
            ("AdIds", "Ad"),
        ):
            for obj_id in modified.get(key) or []:
                rows.append(
                    {
                        "_account": entry.login,
                        "Статус": "Изменён",
                        "Тип": label,
                        "Id": obj_id,
                        "BorderDate": "—",
                    }
                )
        for section, label in (
            ("NotFound", "НеНайден"),
            ("Unprocessed", "НеОбработан"),
        ):
            block = payload.get(section) or {}
            for key, obj_label in (
                ("CampaignIds", "Campaign"),
                ("AdGroupIds", "AdGroup"),
                ("AdIds", "Ad"),
            ):
                for obj_id in block.get(key) or []:
                    rows.append(
                        {
                            "_account": entry.login,
                            "Статус": label,
                            "Тип": obj_label,
                            "Id": obj_id,
                            "BorderDate": "—",
                        }
                    )
        if payload.get("Timestamp"):
            next_marks.append(f"{entry.login}: {payload['Timestamp']}")
    display = (["_account"] if len(entries) > 1 else []) + columns
    context = (
        f"{mark}changes_check: {', '.join(e.login for e in entries)}, с {timestamp}."
    )
    out = finalize(
        ctx,
        context,
        "changes_check",
        display,
        rows,
        params.limit,
        params.save_as,
        errors,
        money_cols=(),
        output=params.output,
        format=params.format,
        account=params.account,
    )
    if next_marks:
        out += "\n\nСледующая метка: " + "; ".join(next_marks) + "."
    return out
