"""Read-only catalog actions for Yandex Cloud Wordstat API v2.

Wordstat uses a Yandex Cloud AI Studio API key, configured separately from
the OAuth token used by Direct.  The four actions mirror the public REST
methods: GetTop, GetDynamics, GetRegionsDistribution and GetRegionsTree.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from directai_mcp.api.wordstat import WordstatError
from directai_mcp.catalog.common import finalize
from directai_mcp.catalog.registry import Ctx, action

DEVICES = (
    "DEVICE_ALL",
    "DEVICE_DESKTOP",
    "DEVICE_PHONE",
    "DEVICE_TABLET",
)
PERIODS = ("PERIOD_DAILY", "PERIOD_WEEKLY", "PERIOD_MONTHLY")
REGION_LEVELS = ("REGION_ALL", "REGION_REGIONS", "REGION_CITIES")


class _WordstatParams(BaseModel):
    """Shared display/export parameters; there is no Direct account here."""

    limit: int | None = None
    save_as: Literal["csv", "md"] | None = Field(
        default=None,
        description="Устарел, используйте output/format: save_as=X ≡ output=file, format=X.",
    )
    output: Literal["inline", "file"] = "inline"
    format: Literal["json", "md", "csv"] = "json"
    dump_dir: str | None = None
    dump_tag: str | None = None

    @field_validator("limit")
    @classmethod
    def _limit(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("limit must be positive")
        return value


class _PhraseParams(_WordstatParams):
    phrase: str = Field(min_length=1, description="Ключевая фраза Wordstat.")
    regions: list[str] = Field(
        default_factory=list,
        description="ID регионов; пусто — все регионы.",
    )
    devices: list[str] = Field(
        default_factory=lambda: ["DEVICE_ALL"],
        description="DEVICE_ALL, DEVICE_DESKTOP, DEVICE_PHONE или DEVICE_TABLET.",
    )

    @field_validator("phrase")
    @classmethod
    def _phrase(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("phrase must not be empty")
        if len(value) > 400:
            raise ValueError("phrase must be at most 400 characters")
        return value

    @field_validator("regions", "devices", mode="before")
    @classmethod
    def _list_values(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            raise ValueError("must be a list")  # noqa: TRY004
        return [str(item).strip() for item in value if str(item).strip()]

    @field_validator("regions")
    @classmethod
    def _regions_limit(cls, value: list[str]) -> list[str]:
        if len(value) > 100:
            raise ValueError("regions must contain at most 100 IDs")
        return value

    @field_validator("devices")
    @classmethod
    def _devices(cls, value: list[str]) -> list[str]:
        if len(value) > 3:
            raise ValueError("devices must contain at most 3 values")
        bad = sorted(set(value) - set(DEVICES))
        if bad:
            raise ValueError(f"devices must be one of {', '.join(DEVICES)}")
        return value or ["DEVICE_ALL"]


class WordstatTopParams(_PhraseParams):
    num_phrases: int = Field(
        default=50,
        ge=1,
        le=2000,
        description="Количество фраз в ответе, максимум 2000.",
    )


class WordstatDynamicsParams(_PhraseParams):
    period: Literal["PERIOD_DAILY", "PERIOD_WEEKLY", "PERIOD_MONTHLY"] = (
        "PERIOD_WEEKLY"
    )
    from_date: str = Field(
        default="",
        description=(
            "Начало периода в RFC3339; пусто — последние 30 дней для daily, "
            "последние 4 завершённые недели для weekly или последний завершённый месяц."
        ),
    )
    to_date: str = Field(
        default="",
        description=(
            "Конец периода в RFC3339; пусто — последний доступный момент API. "
            "Можно не задавать при указанном from_date."
        ),
    )

    @field_validator("from_date", "to_date")
    @classmethod
    def _date(cls, value: str) -> str:
        if not value:
            return value
        try:
            dt.datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("date must be RFC3339, e.g. 2026-01-01T00:00:00Z") from None
        return value


class WordstatRegionsParams(_PhraseParams):
    region: Literal["REGION_ALL", "REGION_REGIONS", "REGION_CITIES"] = (
        "REGION_ALL"
    )


class WordstatRegionsTreeParams(_WordstatParams):
    pass


def _num(value: object) -> object:
    """Render API int64 strings as integers while preserving odd values."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return value if value is not None else "—"


def _rows(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    values = payload.get(field)
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _client(ctx: Ctx):
    return ctx.wordstat()


def _common_dump(
    ctx: Ctx,
    params: BaseModel,
    action_name: str,
    raw: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    columns: list[str],
    context: str,
    *,
    errors: list[str] | None = None,
    payload_field: str | None = None,
) -> str:
    return finalize(
        ctx,
        context,
        action_name,
        columns,
        rows,
        getattr(params, "limit", None),
        getattr(params, "save_as", None),
        errors or [],
        money_cols=(),
        output=getattr(params, "output", "inline"),
        format=getattr(params, "format", "json"),
        dump_dir=getattr(params, "dump_dir", None),
        dump_tag=getattr(params, "dump_tag", None),
        dump_action=action_name,
        dump_params=params.model_dump(),
        dump_raw={payload_field or action_name: raw},
        dump_fields={"Wordstat": [f"POST /v2/wordstat/{_endpoint(action_name)}"]},
        dump_tally={"pages": 1, "versions": ["wordstat-v2"], "complete": True},
        dump_logins=[],
        dump_scope="wordstat",
    )


def _endpoint(action_name: str) -> str:
    return {
        "wordstat_top": "topRequests",
        "wordstat_dynamics": "dynamics",
        "wordstat_regions": "regions",
        "wordstat_regions_tree": "getRegionsTree",
    }[action_name]


def _dates(params: WordstatDynamicsParams) -> tuple[str, str] | str:
    if params.from_date and not params.to_date:
        # The REST API permits an omitted end date; leave that choice to the
        # service rather than inventing a future boundary.
        start = _parse_date(params.from_date)
        if start is None:
            return "Ошибка: даты должны быть RFC3339 с часовым поясом."
        if start > dt.datetime.now(dt.UTC):
            return "Ошибка: from_date не может быть в будущем."
        if params.period == "PERIOD_WEEKLY" and start.weekday() != 0:
            return "Ошибка: для PERIOD_WEEKLY начало должно быть понедельником."
        if params.period == "PERIOD_MONTHLY" and start.day != 1:
            return "Ошибка: для PERIOD_MONTHLY начало должно быть первым днём месяца."
        return params.from_date, ""
    if params.to_date and not params.from_date:
        return "Ошибка: from_date обязателен, если задан to_date."
    if params.from_date:
        start = _parse_date(params.from_date)
        end = _parse_date(params.to_date)
        if start is None or end is None:
            return "Ошибка: даты должны быть RFC3339 с часовым поясом."
        now = dt.datetime.now(dt.UTC)
        if start > now:
            return "Ошибка: from_date не может быть в будущем."
        if end > now:
            return "Ошибка: to_date не может быть в будущем."
        if start > end:
            return "Ошибка: from_date позже to_date."
        if params.period == "PERIOD_WEEKLY" and (
            start.weekday() != 0 or end.weekday() != 6
        ):
            return "Ошибка: для PERIOD_WEEKLY нужны понедельник и воскресенье."
        if params.period == "PERIOD_MONTHLY" and (
            start.day != 1 or (end + dt.timedelta(days=1)).day != 1
        ):
            return "Ошибка: для PERIOD_MONTHLY нужны первый и последний день месяца."
        return params.from_date, params.to_date

    today = dt.datetime.now(dt.UTC).date()
    if params.period == "PERIOD_DAILY":
        end_date = today - dt.timedelta(days=1)
        start_date = end_date - dt.timedelta(days=29)
    elif params.period == "PERIOD_WEEKLY":
        end_date = today - dt.timedelta(days=today.weekday() + 1)
        start_date = end_date - dt.timedelta(days=27)
    else:
        first_this_month = today.replace(day=1)
        end_date = first_this_month - dt.timedelta(days=1)
        start_date = end_date.replace(day=1)
    return _date_text(start_date), _date_text(end_date)


def _parse_date(value: str) -> dt.datetime | None:
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(dt.UTC)


def _date_text(value: dt.date) -> str:
    return f"{value.isoformat()}T00:00:00Z"


@action(
    "wordstat_top",
    "read",
    "Wordstat: топ популярных запросов и связанные фразы за последние 30 дней",
    (
        "wordstat",
        "вордстат",
        "частотность",
        "ключевые фразы",
        "поисковые запросы",
        "топ запросов",
        "семантика",
        "ключевые слова",
    ),
    WordstatTopParams,
    provider="wordstat",
)
async def _top(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WordstatTopParams)
    body: dict[str, Any] = {
        "phrase": params.phrase,
        "numPhrases": params.num_phrases,
        "devices": params.devices,
    }
    if params.regions:
        body["regions"] = params.regions
    try:
        payload = await _client(ctx).post("topRequests", body)
    except WordstatError as exc:
        return f"Ошибка Wordstat: {exc}"
    result_items = _rows(payload, "results")
    association_items = _rows(payload, "associations")
    rows = [
        {"Тип": "результат", "Фраза": item.get("phrase", "—"), "Частотность": _num(item.get("count"))}
        for item in result_items
    ]
    rows.extend(
        {"Тип": "связанная", "Фраза": item.get("phrase", "—"), "Частотность": _num(item.get("count"))}
        for item in association_items
    )
    total = _num(payload.get("totalCount"))
    context = (
        f"wordstat_top: {params.phrase}; последние 30 дней; totalCount={total}; "
        f"регионы={params.regions or 'все'}, устройства={','.join(params.devices)}; "
        f"результатов {len(result_items)}, связанных фраз {len(association_items)}."
    )
    return _common_dump(
        ctx,
        params,
        "wordstat_top",
        [payload],
        rows,
        ["Тип", "Фраза", "Частотность"],
        context,
        payload_field="wordstat_top",
    )


@action(
    "wordstat_dynamics",
    "read",
    "Wordstat: динамика частотности фразы по дням, неделям или месяцам",
    (
        "wordstat",
        "вордстат",
        "динамика",
        "частотность",
        "тренд",
        "по дням",
        "по неделям",
        "по месяцам",
    ),
    WordstatDynamicsParams,
    provider="wordstat",
)
async def _dynamics(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WordstatDynamicsParams)
    dates = _dates(params)
    if isinstance(dates, str):
        return dates
    from_date, to_date = dates
    body: dict[str, Any] = {
        "phrase": params.phrase,
        "period": params.period,
        "fromDate": from_date,
        "devices": params.devices,
    }
    if to_date:
        body["toDate"] = to_date
    if params.regions:
        body["regions"] = params.regions
    try:
        payload = await _client(ctx).post("dynamics", body)
    except WordstatError as exc:
        return f"Ошибка Wordstat: {exc}"
    items = _rows(payload, "results")
    rows = [
        {
            "Дата": item.get("date", "—"),
            "Частотность": _num(item.get("count")),
            "Доля": item.get("share", "—"),
        }
        for item in items
    ]
    return _common_dump(
        ctx,
        params,
        "wordstat_dynamics",
        items,
        rows,
        ["Дата", "Частотность", "Доля"],
        f"wordstat_dynamics: {params.phrase}; {from_date}–{to_date or 'до текущего'}; "
        f"{params.period}; регионы={params.regions or 'все'}, "
        f"устройства={','.join(params.devices)}. Для недельной и месячной "
        "детализации поддерживается только оператор '+'.",
    )


@action(
    "wordstat_regions",
    "read",
    "Wordstat: распределение частотности фразы по регионам за последние 30 дней",
    (
        "wordstat",
        "вордстат",
        "регионы",
        "региональная частотность",
        "распределение",
        "география спроса",
    ),
    WordstatRegionsParams,
    provider="wordstat",
)
async def _regions(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WordstatRegionsParams)
    body: dict[str, Any] = {
        "phrase": params.phrase,
        "region": params.region,
        "devices": params.devices,
    }
    try:
        payload = await _client(ctx).post("regions", body)
    except WordstatError as exc:
        return f"Ошибка Wordstat: {exc}"
    items = _rows(payload, "results")
    rows = [
        {
            "Регион": item.get("region", "—"),
            "Частотность": _num(item.get("count")),
            "Доля": item.get("share", "—"),
            "Индекс соответствия": item.get("affinityIndex", "—"),
        }
        for item in items
    ]
    return _common_dump(
        ctx,
        params,
        "wordstat_regions",
        items,
        rows,
        ["Регион", "Частотность", "Доля", "Индекс соответствия"],
        f"wordstat_regions: {params.phrase}; последние 30 дней; "
        f"детализация {params.region}; устройства={','.join(params.devices)}.",
    )


def _flatten_regions(items: list[dict[str, Any]], depth: int = 0) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = item.get("label") or item.get("name") or "—"
        rows.append({"ID": item.get("id", "—"), "Регион": label, "Уровень": depth})
        children = item.get("children")
        if isinstance(children, list):
            rows.extend(_flatten_regions(children, depth + 1))
    return rows


@action(
    "wordstat_regions_tree",
    "read",
    "Wordstat: справочник регионов и их иерархия",
    (
        "wordstat",
        "вордстат",
        "регионы",
        "справочник регионов",
        "коды регионов",
        "география",
    ),
    WordstatRegionsTreeParams,
    provider="wordstat",
)
async def _regions_tree(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, WordstatRegionsTreeParams)
    try:
        payload = await _client(ctx).post("getRegionsTree")
    except WordstatError as exc:
        return f"Ошибка Wordstat: {exc}"
    raw = payload.get("regions")
    items = raw if isinstance(raw, list) else []
    rows = _flatten_regions(items)
    return _common_dump(
        ctx,
        params,
        "wordstat_regions_tree",
        [payload],
        rows,
        ["ID", "Регион", "Уровень"],
        f"wordstat_regions_tree: регионов в дереве {len(rows)}.",
    )
