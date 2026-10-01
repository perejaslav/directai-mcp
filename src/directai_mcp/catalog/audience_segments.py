"""Read-действия API Яндекс Аудиторий, этап 1 (только GET, экспериментально).

Отдельный файл (не catalog/audiences.py): там — условия нацеливания и списки
ретаргетинга Директа (RetargetingLists/AudienceTargets), здесь — сегменты
самих Аудиторий (GET /v1/management/segments).

Два действия: `audience_segments_list` — все сегменты пользователя;
`audience_segment_get` — один сегмент по id (фильтром по списку: отдельного
GET одного сегмента в API нет). Параметра account нет — сегменты принадлежат
владельцу токена, а не кабинету Директа (как Вебмастер, не кабинет).

Токен: отдельный (`directai-mcp set-token --audience`) → иначе основной.
Записи нет сознательно (upload/create/delete/grant — следующие этапы).

Docs: https://yandex.ru/dev/audience/ru/ ,
https://yandex.ru/dev/audience/en/management/formats ,
https://yandex.com/dev/audience/en/intro/authorization .
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from directai_mcp.api.audience import _get
from directai_mcp.api.errors import AudienceError
from directai_mcp.catalog.common import finalize
from directai_mcp.catalog.registry import Ctx, action

SEGMENT_TYPES = (
    "uploading",
    "metrika",
    "appmetrica",
    "lookalike",
    "geo",
    "pixel",
)

SEGMENT_STATUSES = (
    "uploaded",
    "is_processed",
    "processed",
    "is_updated",
    "few_data",
    "processing_failed",
)


def _token(ctx: Ctx) -> str:
    """Токен Аудиторий: отдельный (своё приложение) или основной."""
    from directai_mcp.config import get_audience_token

    resolved = get_audience_token(ctx.settings.auth_login) or ctx.token
    if not resolved:
        raise AudienceError(
            "нет токена Аудиторий: выполните `directai-mcp set-token --audience` "
            "или задайте DIRECTAI_AUDIENCE_TOKEN"
        )
    return resolved


def _size(seg: dict) -> str | int:
    """Размер сегмента: matched_quantity → item_quantity, иначе «—».

    Поля количества в примерах сторонних схем; в оффдоках BaseSegment их нет —
    неподтверждено, поэтому мягкий доступ без KeyError.
    """
    for key in ("matched_quantity", "item_quantity"):
        value = seg.get(key)
        if isinstance(value, int):
            return value
    return "—"


def _details(seg: dict) -> str:
    """Типоспецифичные поля сегмента одной строкой (для segment_get)."""
    kind = seg.get("type")
    keys = {
        "uploading": ("content_type", "hashed", "used_hashing_alg"),
        "metrika": ("metrika_segment_type", "metrika_segment_id"),
        "appmetrica": ("app_metrica_segment_type", "app_metrica_segment_id"),
        "lookalike": (
            "lookalike_link",
            "lookalike_value",
            "maintain_device_distribution",
            "maintain_geo_distribution",
        ),
        "geo": ("geo_segment_type", "times_quantity", "period_length"),
        "pixel": ("pixel_id", "period_length", "times_quantity"),
    }.get(str(kind), ())
    parts = [f"{key}={seg[key]}" for key in keys if key in seg]
    return "; ".join(parts) if parts else "—"


def _row(seg: dict) -> dict:
    return {
        "ID": seg.get("id") or "—",
        "Имя": seg.get("name") or "—",
        "Тип": seg.get("type") or "—",
        "Статус": seg.get("status") or "—",
        "Размер": _size(seg),
        "Создан": seg.get("create_time") or "—",
        "Владелец": seg.get("owner") or "—",
    }


async def _segments(ctx: Ctx) -> list[dict]:
    payload = await _get(_token(ctx), "segments")
    items = payload.get("segments") if isinstance(payload, dict) else None
    if items is None:
        raise AudienceError("нет поля `segments` в ответе GET segments")
    return [s for s in items if isinstance(s, dict)]


class _AudParams(BaseModel):
    """Общие параметры вывода (account нет — Аудитории не кабинет)."""

    limit: int | None = None
    save_as: Literal["csv", "md"] | None = Field(
        default=None,
        description="Устарел, используйте output/format: save_as=X ≡ output=file, format=X.",
    )
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"
    dump_dir: str | None = None
    dump_tag: str | None = None


class AudienceSegmentsListParams(_AudParams):
    pass


@action(
    "audience_segments_list",
    "read",
    "Аудитории: список сегментов пользователя (тип, статус, размер)",
    (
        "аудитории",
        "аудитория",
        "audience",
        "сегменты",
        "segments",
        "lookalike",
        "похожие",
        "метрика",
        "гео",
        "пиксель",
    ),
    AudienceSegmentsListParams,
)
async def _list(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AudienceSegmentsListParams)
    try:
        items = await _segments(ctx)
    except AudienceError as exc:
        return f"Ошибка Аудиторий: {exc}"
    rows = [_row(seg) for seg in items]
    return finalize(
        ctx,
        f"audience_segments_list: сегментов {len(rows)}.",
        "audience_segments_list",
        ["ID", "Имя", "Тип", "Статус", "Размер", "Создан", "Владелец"],
        rows,
        params.limit,
        params.save_as,
        [],
        money_cols=(),
        output=params.output,
        format=params.format,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="audience_segments_list",
        dump_params=params.model_dump(),
        dump_raw={"audience_segments_list": [
            dict(s, linked_to_campaign=False) for s in items]},
        dump_fields={"GET": ["segments"]},
        dump_tally={"pages": 1, "versions": ["audience-v1"],
                    "complete": True},
        dump_logins=[],
        dump_scope="cabinet",
    )


class AudienceSegmentGetParams(_AudParams):
    segment_id: int = Field(description="ID сегмента из audience_segments_list")


@action(
    "audience_segment_get",
    "read",
    "Аудитории: один сегмент по id (тип, статус, размер, детали)",
    (
        "аудитории",
        "аудитория",
        "audience",
        "сегмент",
        "segment",
        "lookalike",
        "статус сегмента",
    ),
    AudienceSegmentGetParams,
)
async def _one(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, AudienceSegmentGetParams)
    try:
        items = await _segments(ctx)
    except AudienceError as exc:
        return f"Ошибка Аудиторий: {exc}"
    found: dict[str, Any] | None = None
    for seg in items:
        if seg.get("id") == params.segment_id:
            found = seg
            break
    if found is None:
        return (
            f"Сегмент {params.segment_id} не найден "
            f"среди {len(items)} доступных."
        )
    row = _row(found)
    row["Детали"] = _details(found)
    return finalize(
        ctx,
        f"audience_segment_get: сегмент {params.segment_id}.",
        "audience_segment_get",
        ["ID", "Имя", "Тип", "Статус", "Размер", "Создан", "Владелец",
         "Детали"],
        [row],
        params.limit,
        params.save_as,
        [],
        money_cols=(),
        output=params.output,
        format=params.format,
        dump_dir=params.dump_dir,
        dump_tag=params.dump_tag,
        dump_action="audience_segment_get",
        dump_params=params.model_dump(),
        dump_raw={"audience_segment_get": [
            dict(found, linked_to_campaign=False)]},
        dump_fields={"GET": ["segments"], "filter": ["segment_id"]},
        dump_tally={"pages": 1, "versions": ["audience-v1"],
                    "complete": True},
        dump_logins=[],
        dump_scope="cabinet",
    )
