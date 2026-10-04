"""Запись целей Метрики (v1.17.0): create / update / delete.

Три действия, только через общий поток `plan_write` → согласие →
`apply_write`: `metrika_goal_create`, `metrika_goal_update`,
`metrika_goal_delete`. Свойства плана не ослаблены: `plan_id` (TTL 15 минут),
`acknowledge_warnings`, `owner_confirmed` — как у остальных записей.

Транспорт — `api/metrika.py` (POST/PUT/DELETE + свежий GET, без
сокетного кеша `catalog/metrika_goals.py`: read-back обязан читать живое
состояние, а не кеш процесса).

Поддержка (Management API, GoalE — 13 типов):
- `action` — JS-событие: `conditions[].operator` = exact|contain|start|regexp;
- `url` — посещение страниц; условие = `exact|contain|start|regexp`, плюс
  `action|regexp_action|contain_action` (только для составных целей);
  несколько условий — «ИЛИ» (док: «выполняется хотя бы одно из условий»);
- `email`, `messenger`, `search`, `phone` — клик по условию (у `phone` ещё
  `hide_phone_number`);
- `file` — скачивание файлов: любой файл или конкретный `file_url`;
- `social` — переход в соцсеть: любая или конкретная `social_url`;
- `step` — составная цель: 2–5 шагов, шаг = `action` или `url`;
- `number` — глубина (`depth` ≥ 2), `visit_duration` — длительность в секундах.

Не поддержано сознательно (в DirectAI — до API не доходит, с текстом):
- `multi` («Мультицель», несколько условий через ИЛИ) — типа `multi` в
  Management API нет: GoalE перечисляет 13 типов, мультицель есть только в
  интерфейсе Метрики (справка: yandex.ru/support/metrica/ru/general/multi.html).
  Эквивалент через API — несколько `conditions` в одной цели, они и работают
  по «ИЛИ»;
- `chat` (условия по полям/платформе/тегу чата) и `payment_system` (цели
  Метрика создаёт сама) — не входят в задачу v1.17.0.

Проверки до записи (prepare) — по живым данным, до обращения к API:
- точный `counter_id` и право записи: `GET counter/{id}` → `permission`
  (`own`/`edit`; `view` — только просмотр, запись отклоняется);
- лимит счётчика: до 200 целей (справка Метрики);
- точный дубль: тот же тип + те же условия → отказ с id существующей цели;
- `step`: 2–5 шагов (лимит интерфейса; в схеме API не документирован).

Read-back после каждой записи: свежий GET списка целей, сверка типа,
условий и имени (id новой цели — из ответа POST). При таймауте записи
результат неизвестен: повторять вслепую нельзя, поэтому статус `unverified`
и решение — по перечитанным целям.

Docs: https://yandex.com/dev/metrika/ru/management/openapi/goal/addGoal ,
https://yandex.com/dev/metrika/ru/management/openapi/goal/editGoal ,
https://yandex.com/dev/metrika/ru/management/openapi/goal/deleteGoal ,
https://yandex.com/dev/metrika/ru/management/openapi/goal/goals ,
https://yandex.com/dev/metrika/ru/management/openapi/counter/counter ,
https://yandex.ru/support/metrica/ru/general/goals.html .
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from directai_mcp.api import metrika as mk
from directai_mcp.api.errors import MetrikaApiError
from directai_mcp.catalog.registry import Ctx, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.safety.guard import require_owner_confirm

#: Справка Метрики: для каждого счётчика можно задать до 200 целей.
MAX_GOALS_PER_COUNTER = 200

#: Составная цель: 2–5 шагов (лимит интерфейса Метрики).
MIN_STEPS = 2
MAX_STEPS = 5

#: Операторы условий (ActionGoalCondition, UrlGoalCondition).
OPERATORS = ("contain", "exact", "start", "regexp")
#: + операторы, которые документированы «для составных целей».
STEP_OPERATORS = OPERATORS + ("action", "regexp_action", "contain_action")

#: Длины значения `url` по документации условия (Max length).
VALUE_MAX: dict[str, int] = {
    "action": 16384,
    "url": 16384,
    "email": 1024,
    "phone": 25,
    "messenger": 16384,
    "search": 16384,
}

#: Типы с условиями `operator`/`value` и допустимые операторы.
CONDITION_TYPES: dict[str, tuple[str, ...]] = {
    "action": OPERATORS,
    "url": STEP_OPERATORS,
    "email": OPERATORS,
    "phone": OPERATORS,
    "messenger": OPERATORS,
    "search": OPERATORS,
}

#: Типы целей, которые создаём.
GOAL_TYPES = tuple(CONDITION_TYPES) + (
    "file", "social", "step", "number", "visit_duration",
)

#: Типы, которые в API есть, но в DirectAI не заведены (текст для агента).
UNSUPPORTED_TYPES: dict[str, str] = {
    "multi": (
        "Мультицели в Management API нет: GoalE перечисляет 13 типов без "
        "«multi» (мультицель — только интерфейс Метрики). Несколько условий "
        "через ИЛИ задайте в одной цели: type=url/action и несколько "
        "conditions — достигается при выполнении любого из них."
    ),
    "chat": (
        "цель типа chat (события чата) DirectAI не создаёт: условия "
        "задаются по полям чата (answered/platform/tag). Создайте в "
        "интерфейсе Метрики."
    ),
    "payment_system": (
        "цели типа payment_system создаёт сама Метрика (возврат из "
        "платёжной системы); вручную их не создают."
    ),
}

_NAME_MAX = 255

DELETE_DANGER = (
    "необратимо: собранная по цели статистика пропадёт из отчётов "
    "(справка Метрики)"
)


def unverified_lines(exc: MetrikaApiError) -> list[str]:
    """Таймаут/обрыв на записи: результат неизвестен, повтор вслепую опасен."""
    return [
        f"Метрика: {exc}",
        (
            "Запись НЕ повторяем вслепую: результат определит read-back "
            "по целям счётчика."
        ),
    ]


# --------------------------------------------------------------------------
# сериализация
# --------------------------------------------------------------------------


def cond_items(items: list[GoalCondition]) -> list[dict[str, str]]:
    """GoalCondition -> условия API [{type, url}]."""
    return [{"type": c.operator, "url": c.value} for c in items]


def api_cond_items(conditions: Any) -> list[dict[str, str]]:
    """Условия из ответа API -> [{type, url}] (без url у all_files)."""
    out: list[dict[str, str]] = []
    for item in conditions or []:
        if not isinstance(item, dict):
            continue
        cond: dict[str, str] = {"type": str(item.get("type") or "")}
        if item.get("url") is not None:
            cond["url"] = str(item["url"])
        out.append(cond)
    return out


def conditions_key(conditions: Any, ordered: bool = False) -> tuple:
    """Ключ сравнения условий: оператор + значение без краёв.

    Условия одной цели объединены «ИЛИ», поэтому порядок не значим;
    у шагов составной цели порядок значим (поэтому ordered=True).
    """
    pairs = [
        (str(c.get("type") or ""), str(c.get("url") or "").strip())
        for c in api_cond_items(conditions)
    ]
    return tuple(pairs) if ordered else tuple(sorted(pairs))


def goal_fingerprint(goal: dict) -> tuple:
    """(тип, условия) — по ним определяется точный дубль цели."""
    gtype = str(goal.get("type") or "")
    if gtype == "number":
        return (gtype, goal.get("depth"))
    if gtype == "visit_duration":
        return (gtype, goal.get("duration"))
    if gtype == "step":
        steps = goal.get("steps") or []
        return (
            gtype,
            tuple(
                (
                    str(step.get("type") or ""),
                    conditions_key(step.get("conditions"), ordered=True),
                )
                for step in steps
                if isinstance(step, dict)
            ),
        )
    return (gtype, conditions_key(goal.get("conditions")))


def find_duplicate(goals: list[dict], body: dict) -> dict | None:
    """Существующая цель с тем же типом и теми же условиями, иначе None."""
    wanted = goal_fingerprint(body)
    for goal in goals:
        if goal_fingerprint(goal) == wanted:
            return goal
    return None


def live_goal_body(goal: dict) -> dict:
    """Живая цель -> тело PUT: PUT в API заменяет цель целиком."""
    body: dict[str, Any] = {
        "id": int(goal["id"]),
        "name": str(goal.get("name") or ""),
        "type": str(goal.get("type") or ""),
    }
    if goal.get("default_price") is not None:
        body["default_price"] = goal["default_price"]
    if goal.get("is_favorite") is not None:
        body["is_favorite"] = bool(goal["is_favorite"])
    for key in ("depth", "duration", "hide_phone_number"):
        if goal.get(key) is not None:
            body[key] = goal[key]
    steps = goal.get("steps")
    if isinstance(steps, list) and steps:
        body["steps"] = [
            {
                "type": str(step.get("type") or ""),
                "conditions": api_cond_items(step.get("conditions")),
            }
            for step in steps
            if isinstance(step, dict)
        ]
    else:
        body["conditions"] = api_cond_items(goal.get("conditions"))
    return body


# --------------------------------------------------------------------------
# валидация условий
# --------------------------------------------------------------------------


def _validate_conditions(gtype: str, items: Any, where: str) -> None:
    allowed = CONDITION_TYPES.get(gtype)
    if allowed is None:
        raise ValueError(f"{where}тип {gtype}: условий operator/value не требует.")
    if not items:
        raise ValueError(f"{where}тип {gtype}: нужен хотя бы один параметр "
                         "conditions (operator + value).")
    limit = VALUE_MAX.get(gtype, 0)
    for item in items:
        operator = str(item.operator)
        if operator not in allowed:
            raise ValueError(
                f"{where}тип {gtype}: оператор {operator!r} не поддерживается "
                f"(допустимо: {', '.join(allowed)})."
            )
        value = str(item.value)
        if not value.strip():
            raise ValueError(f"{where}тип {gtype}: значение условия пустое.")
        if limit and len(value) > limit:
            raise ValueError(
                f"{where}тип {gtype}: значение длиной {len(value)} "
                f"символов, в API допустимо до {limit}."
            )


def _validate_steps(steps: Any) -> None:
    if not steps:
        raise ValueError("тип step: нужны steps (шаги составной цели).")
    if not MIN_STEPS <= len(steps) <= MAX_STEPS:
        raise ValueError(
            f"тип step: шагов {len(steps)}, допустимо {MIN_STEPS}–{MAX_STEPS}."
        )
    for index, step in enumerate(steps, 1):
        where = f"тип step, шаг {index}: "
        if step.type not in ("action", "url"):
            raise ValueError(
                f"{where}тип шага {step.type!r} не поддерживается "
                "(в составной цели шаг — action или url)."
            )
        allowed = STEP_OPERATORS if step.type == "url" else OPERATORS
        if not step.conditions:
            raise ValueError(f"{where}нужно хотя бы одно условие.")
        limit = VALUE_MAX.get(step.type, 0)
        for item in step.conditions:
            if item.operator not in allowed:
                raise ValueError(
                    f"{where}оператор {item.operator!r} не поддерживается "
                    f"(допустимо: {', '.join(allowed)})."
                )
            if not str(item.value).strip():
                raise ValueError(f"{where}значение условия пустое.")
            if limit and len(str(item.value)) > limit:
                raise ValueError(
                    f"{where}значение длиннее {limit} символов."
                )


#: Параметр -> типы целей, к которым он применим (остальное отклоняется).
PARAM_TYPES: dict[str, tuple[str, ...]] = {
    "conditions": tuple(CONDITION_TYPES),
    "hide_phone_number": ("phone",),
    "file_all": ("file",),
    "file_url": ("file",),
    "social_all": ("social",),
    "social_url": ("social",),
    "steps": ("step",),
    "depth": ("number",),
    "duration": ("visit_duration",),
}


# --------------------------------------------------------------------------
# модели параметров
# --------------------------------------------------------------------------


class GoalCondition(BaseModel):
    operator: str = Field(
        description="оператор условия: exact|contain|start|regexp "
        "(для составных целей url ещё action|regexp_action|contain_action)"
    )
    value: str = Field(description="значение условия (для API — url)")


class GoalStep(BaseModel):
    type: Literal["action", "url"] = Field(
        description="тип шага составной цели: action (JS-событие) или url "
        "(посещение страниц)"
    )
    conditions: list[GoalCondition] = Field(
        description="условия шага (ИЛИ между собой)"
    )


class _GoalBase(BaseModel):
    counter_id: int = Field(description="точный id счётчика Метрики (обязателен)")
    name: str = Field(description=f"название цели (1–{_NAME_MAX} символов)")


class MetrikaGoalCreateParams(_GoalBase):
    type: str = Field(
        description="тип цели: action|url|phone|messenger|email|file|social|"
        "search|step|number|visit_duration"
    )
    default_price: float | None = Field(
        default=None, description="цена цели по умолчанию (доход), руб."
    )
    is_favorite: bool | None = Field(default=None, description="избранная цель")
    conditions: list[GoalCondition] | None = Field(
        default=None,
        description="условия через ИЛИ для action|url|email|phone|messenger|"
        "search: [{operator, value}]",
    )
    file_all: bool | None = Field(
        default=None, description="file: любой скачиваемый файл"
    )
    file_url: str | None = Field(
        default=None, description="file: url конкретного файла"
    )
    social_all: bool | None = Field(
        default=None, description="social: любая соцсеть"
    )
    social_url: str | None = Field(
        default=None, description="social: url конкретной соцсети"
    )
    steps: list[GoalStep] | None = Field(
        default=None, description=f"step: {MIN_STEPS}–{MAX_STEPS} шагов по порядку"
    )
    depth: int | None = Field(default=None, description="number: глубина ≥ 2")
    duration: int | None = Field(
        default=None, description="visit_duration: длительность визита, секунды"
    )
    hide_phone_number: bool | None = Field(
        default=None, description="phone: скрывать номер на десктопах"
    )

    @model_validator(mode="after")
    def _check(self) -> MetrikaGoalCreateParams:
        gtype = str(self.type)
        if gtype in UNSUPPORTED_TYPES:
            raise ValueError(UNSUPPORTED_TYPES[gtype])
        if gtype not in GOAL_TYPES:
            raise ValueError(
                f"тип цели {gtype!r} неизвестен; доступны: "
                f"{', '.join(GOAL_TYPES)}."
            )
        if not self.name.strip():
            raise ValueError("название цели пустое.")
        if len(self.name) > _NAME_MAX:
            raise ValueError(
                f"название цели длиннее {_NAME_MAX} символов "
                f"({len(self.name)})."
            )
        if self.default_price is not None and self.default_price < 0:
            raise ValueError("default_price не может быть отрицательным.")
        for key, types in PARAM_TYPES.items():
            value = getattr(self, key)
            if value is None or value is False:
                continue
            if gtype not in types:
                raise ValueError(
                    f"параметр {key} не применим к типу цели {gtype} "
                    f"(только к: {', '.join(types)})."
                )
        if gtype in CONDITION_TYPES:
            _validate_conditions(gtype, self.conditions, "")
        elif gtype == "file":
            if bool(self.file_all) == bool(self.file_url):
                raise ValueError(
                    "тип file: укажите либо file_all=true, либо file_url "
                    "(ровно одно из двух)."
                )
            if self.file_url is not None and not str(self.file_url).strip():
                raise ValueError("тип file: file_url пустой.")
        elif gtype == "social":
            if bool(self.social_all) == bool(self.social_url):
                raise ValueError(
                    "тип social: укажите либо social_all=true, либо social_url "
                    "(ровно одно из двух)."
                )
            if self.social_url is not None and not str(self.social_url).strip():
                raise ValueError("тип social: social_url пустой.")
        elif gtype == "step":
            _validate_steps(self.steps)
        elif gtype == "number":
            if self.depth is None:
                raise ValueError("тип number: нужен depth (глубина ≥ 2).")
            if self.depth < 2:
                raise ValueError(f"тип number: depth={self.depth}, минимум 2.")
        elif gtype == "visit_duration":
            if self.duration is None:
                raise ValueError(
                    "тип visit_duration: нужен duration (секунды ≥ 1)."
                )
            if self.duration < 1:
                raise ValueError(
                    f"тип visit_duration: duration={self.duration}, минимум 1."
                )
        return self


class MetrikaGoalUpdateParams(BaseModel):
    counter_id: int = Field(description="точный id счётчика Метрики (обязателен)")
    goal_id: int = Field(description="id изменяемой цели")
    name: str | None = Field(default=None, description="новое название")
    default_price: float | None = Field(
        default=None, description="новая цена цели по умолчанию (доход), руб."
    )
    is_favorite: bool | None = Field(default=None, description="избранная цель")
    conditions: list[GoalCondition] | None = Field(
        default=None, description="новые условия (полная замена списка)"
    )
    depth: int | None = Field(default=None, description="number: новая глубина")
    duration: int | None = Field(
        default=None, description="visit_duration: новая длительность, секунды"
    )

    @model_validator(mode="after")
    def _check(self) -> MetrikaGoalUpdateParams:
        changed = [
            key
            for key in (
                "name", "default_price", "is_favorite", "conditions",
                "depth", "duration",
            )
            if getattr(self, key) is not None
        ]
        if not changed:
            raise ValueError(
                "нечего менять: укажите name, default_price, is_favorite, "
                "conditions, depth или duration."
            )
        if self.name is not None:
            if not self.name.strip():
                raise ValueError("название цели пустое.")
            if len(self.name) > _NAME_MAX:
                raise ValueError(
                    f"название цели длиннее {_NAME_MAX} символов."
                )
        if self.default_price is not None and self.default_price < 0:
            raise ValueError("default_price не может быть отрицательным.")
        if self.depth is not None and self.depth < 2:
            raise ValueError(f"depth={self.depth}, минимум 2.")
        if self.duration is not None and self.duration < 1:
            raise ValueError(f"duration={self.duration}, минимум 1.")
        return self


class MetrikaGoalDeleteParams(BaseModel):
    counter_id: int = Field(description="точный id счётчика Метрики (обязателен)")
    goal_id: int = Field(description="id удаляемой цели")


# --------------------------------------------------------------------------
# сборка тела и тексты
# --------------------------------------------------------------------------


def build_goal_body(params: MetrikaGoalCreateParams) -> dict:
    """Параметры -> тело цели для POST (GoalE конкретного типа)."""
    body: dict[str, Any] = {"name": params.name, "type": params.type}
    if params.default_price is not None:
        body["default_price"] = params.default_price
    if params.is_favorite is not None:
        body["is_favorite"] = params.is_favorite
    gtype = params.type
    if gtype in CONDITION_TYPES:
        body["conditions"] = cond_items(params.conditions or [])
        if gtype == "phone" and params.hide_phone_number is not None:
            body["hide_phone_number"] = params.hide_phone_number
    elif gtype == "file":
        if params.file_all:
            body["conditions"] = [{"type": "all_files"}]
        else:
            body["conditions"] = [{"type": "file", "url": params.file_url}]
    elif gtype == "social":
        if params.social_all:
            body["conditions"] = [{"type": "all_social"}]
        else:
            body["conditions"] = [{"type": "social", "url": params.social_url}]
    elif gtype == "step":
        body["steps"] = [
            {"type": step.type, "conditions": cond_items(step.conditions)}
            for step in (params.steps or [])
        ]
    elif gtype == "number":
        body["depth"] = params.depth
    elif gtype == "visit_duration":
        body["duration"] = params.duration
    return body


def _cond_lines(conditions: Any, steps: Any = None) -> list[str]:
    if steps:
        out: list[str] = []
        for index, step in enumerate(steps, 1):
            for position, cond in enumerate(
                api_cond_items(step.get("conditions")), 1
            ):
                out.append(
                    f"{index}. {position}) {step.get('type')}: "
                    f"{cond['type']} «{cond.get('url', '')}»"
                )
        return out
    out = []
    for position, cond in enumerate(api_cond_items(conditions), 1):
        out.append(
            f"{position}) {cond['type']} «{cond.get('url', '')}»"
        )
    return out


def describe_goal_body(body: dict) -> list[str]:
    """Тело цели -> строки предпросмотра (человеческим языком)."""
    gtype = str(body.get("type") or "")
    lines = [
        f"- имя: {body.get('name')}",
        f"- тип: {gtype} ({GOAL_LABELS.get(gtype, gtype)})",
    ]
    if body.get("depth") is not None:
        lines.append(f"- глубина просмотров: {body['depth']}")
    if body.get("duration") is not None:
        lines.append(f"- длительность визита: {body['duration']} с")
    if body.get("hide_phone_number") is not None:
        lines.append(
            "- скрывать номер на десктопах: "
            + ("да" if body["hide_phone_number"] else "нет")
        )
    if body.get("steps") is not None:
        lines.append(f"- шагов (по порядку): {len(body['steps'])}")
    conds = _cond_lines(body.get("conditions"), body.get("steps"))
    if conds:
        lines.append("- условия (между собой ИЛИ):")
        lines += [f"  {line}" for line in conds]
    if body.get("default_price") is not None:
        lines.append(f"- цена по умолчанию: {body['default_price']}")
    if body.get("is_favorite") is not None:
        lines.append(
            "- избранная: " + ("да" if body["is_favorite"] else "нет")
        )
    return lines


GOAL_LABELS = {
    "action": "целевое событие (JS-событие)",
    "url": "посещение страниц",
    "email": "клик по email",
    "phone": "клик по номеру телефона",
    "messenger": "переход в мессенджер",
    "file": "скачивание файлов",
    "social": "переход в соцсеть",
    "search": "поиск по сайту",
    "step": "составная цель",
    "number": "количество просмотров",
    "visit_duration": "продолжительность визита",
}


# --------------------------------------------------------------------------
# живые чтения (без кеша процесса)
# --------------------------------------------------------------------------


async def _goals(token: str, counter_id: int) -> list[dict]:
    """Свежий список целей счётчика (Management API, без кеша)."""
    try:
        payload = await mk.get(
            token, f"/management/v1/counter/{counter_id}/goals"
        )
    except MetrikaApiError as exc:
        raise ValueError(f"счётчик {counter_id}: {exc}") from None
    if isinstance(payload, dict) and isinstance(payload.get("goals"), list):
        return [g for g in payload["goals"] if isinstance(g, dict)]
    raise ValueError(f"счётчик {counter_id}: в ответе нет списка целей (goals).")


async def _require_editable(token: str, counter_id: int) -> str:
    """Право записи по живому счётчику: own|edit. Иначе отказ до записи."""
    try:
        payload = await mk.get(token, f"/management/v1/counter/{counter_id}")
    except MetrikaApiError as exc:
        raise ValueError(f"счётчик {counter_id}: {exc}") from None
    counter = payload.get("counter") if isinstance(payload, dict) else {}
    if not isinstance(counter, dict):
        counter = {}
    permission = str(counter.get("permission") or "")
    if permission in ("own", "edit"):
        return permission
    if permission == "view":
        raise ValueError(
            f"счётчик {counter_id}: у вас доступ только просмотр "
            "(permission=view) — запись целей невозможна."
        )
    raise ValueError(
        f"счётчик {counter_id}: не удалось подтвердить право записи "
        f"(permission={permission or 'поле отсутствует'})."
    )


async def _find_live(
    goals: list[dict], goal_id: int, body: dict, name: str
) -> dict | None:
    """Цель после записи: по id, иначе по имени + отпечатку."""
    for goal in goals:
        if goal.get("id") == goal_id:
            return goal
    for goal in goals:
        if str(goal.get("name") or "") == name \
                and goal_fingerprint(goal) == goal_fingerprint(body):
            return goal
    return None


def _diff_notes(before: dict, after: dict) -> list[str]:
    """Что именно меняется в цели (для предпросмотра и read-back)."""
    notes: list[str] = []
    if str(before.get("name") or "") != str(after.get("name") or ""):
        notes.append(
            f"имя: «{before.get('name')}» → «{after.get('name')}»"
        )
    if before.get("default_price") != after.get("default_price"):
        notes.append(
            f"цена по умолчанию: {before.get('default_price')} → "
            f"{after.get('default_price')}"
        )
    if bool(before.get("is_favorite")) != bool(after.get("is_favorite")):
        notes.append(
            f"избранная: {'да' if before.get('is_favorite') else 'нет'} → "
            f"{'да' if after.get('is_favorite') else 'нет'}"
        )
    if goal_fingerprint(before) != goal_fingerprint(after):
        old = _cond_lines(before.get("conditions"), before.get("steps"))
        new = _cond_lines(after.get("conditions"), after.get("steps"))
        notes.append(
            "условия: " + ("; ".join(old) or "нет") + "  →  "
            + ("; ".join(new) or "нет")
        )
        if before.get("depth") != after.get("depth"):
            notes.append(
                f"глубина: {before.get('depth')} → {after.get('depth')}"
            )
        if before.get("duration") != after.get("duration"):
            notes.append(
                f"длительность: {before.get('duration')} → "
                f"{after.get('duration')} с"
            )
    return notes


# --------------------------------------------------------------------------
# metrika_goal_create
# --------------------------------------------------------------------------


async def _prepare_create(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, MetrikaGoalCreateParams)
    token = ctx.require_metrika_write()
    permission = await _require_editable(token, params.counter_id)
    goals = await _goals(token, params.counter_id)
    if len(goals) >= MAX_GOALS_PER_COUNTER:
        raise ValueError(
            f"счётчик {params.counter_id}: уже {len(goals)} целей, а лимит "
            f"Метрики — {MAX_GOALS_PER_COUNTER}. Запись отклонена до API."
        )
    body = build_goal_body(params)
    duplicate = find_duplicate(goals, body)
    if duplicate is not None:
        raise ValueError(
            
                "дубль: у счётчика уже есть цель с тем же типом и теми же "
                f"условиями — id {duplicate.get('id')} "
                f"«{duplicate.get('name')}». Измените её через "
                "metrika_goal_update или задайте другое условие."
            
        )
    lines = [
        (
            f"Цель будет создана в счётчике Метрики {params.counter_id} "
            f"(право: {permission}, целей сейчас: {len(goals)}):"
        ),
    ]
    lines += describe_goal_body(body)
    warnings = [
        (
            "новая цель не влияет на уже настроенную оптимизацию: её нужно "
            "выбрать в кампании (campaigns_update: priority_goals)."
        ),
    ]
    if body["type"] == "step":
        warnings.append(
            
                "шаги составной цели должны идти в порядке выполнения: "
                "каждый шаг засчитывается только после всех предыдущих."
            
        )
    return {
        "before": {
            "counter_id": params.counter_id,
            "permission": permission,
            "goals_total": len(goals),
        },
        "requests": [(mk.METRIKA, "counter/goals", {"goal": body})],
        "preview": "\n".join(lines),
        "warnings": warnings,
    }


async def _apply_create(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    counter_id = int(plan.params["counter_id"])
    body = plan.requests[0][2]["goal"]
    try:
        payload = await mk.post(
            ctx.require_metrika_write(),
            f"/management/v1/counter/{counter_id}/goals",
            {"goal": body},
        )
    except MetrikaApiError as exc:
        if exc.unverified:
            return {
                "status": "unverified",
                "lines": unverified_lines(exc),
                "response": {"counter_id": counter_id, "goal": body,
                             "unverified": True},
            }
        return {
            "status": "failed",
            "lines": [f"Ошибка API Метрики: {exc}"],
            "response": {"counter_id": counter_id, "error": str(exc)},
        }
    goal = payload.get("goal") if isinstance(payload, dict) else None
    if not isinstance(goal, dict) or goal.get("id") is None:
        return {
            "status": "failed",
            "lines": [
                (
                    "API Метрики не вернула созданную цель (нет goal.id): "
                    "проверьте список целей счётчика."
                )
            ],
            "response": {"counter_id": counter_id, "payload": payload},
        }
    return {
        "status": "applied",
        "lines": [f"цель {goal['id']} «{goal.get('name')}» создана."],
        "response": {"counter_id": counter_id, "goal_id": goal["id"]},
    }


async def _verify_create(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    counter_id = int(plan.params["counter_id"])
    body = plan.requests[0][2]["goal"]
    last = getattr(plan, "last_response", None) or {}
    goal_id = last.get("goal_id") if isinstance(last, dict) else None
    try:
        goals = await _goals(ctx.require_metrika_write(), counter_id)
    except ValueError as exc:
        return {"after": None, "ok": False, "note": f"read-back не удался: {exc}"}
    found = await _find_live(goals, goal_id, body, str(body.get("name")))
    if found is None:
        return {
            "after": {"counter_id": counter_id, "goal_id": goal_id},
            "ok": False,
            "note": (
                "read-back НЕ подтвердил: цели «"
                f"{body.get('name')}» в списке счётчика {counter_id} нет."
            ),
        }
    if goal_fingerprint(found) != goal_fingerprint(body):
        return {
            "after": {"goal": found},
            "ok": False,
            "note": (
                f"read-back: цель {found.get('id')} найдена, но тип/условия "
                "отличаются от запрошенных."
            ),
        }
    return {
        "after": {"goal": found},
        "ok": True,
        "note": (
            f"подтверждено read-back: цель {found.get('id')} «"
            f"{found.get('name')}», тип {found.get('type')}, "
            "условия совпали."
        ),
    }


# --------------------------------------------------------------------------
# metrika_goal_update
# --------------------------------------------------------------------------


async def _prepare_update(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, MetrikaGoalUpdateParams)
    token = ctx.require_metrika_write()
    permission = await _require_editable(token, params.counter_id)
    goals = await _goals(token, params.counter_id)
    live = next(
        (g for g in goals if g.get("id") == params.goal_id), None
    )
    if live is None:
        raise ValueError(
            f"цель {params.goal_id} не найдена в счётчике "
            f"{params.counter_id} — запись отклонена до API."
        )
    gtype = str(live.get("type") or "")
    if gtype not in GOAL_TYPES:
        raise ValueError(
            f"цель {params.goal_id} типа {gtype!r}: DirectAI не умеет "
            "изменять цели этого типа."
        )
    body = live_goal_body(live)
    if params.name is not None:
        body["name"] = params.name
    if params.default_price is not None:
        body["default_price"] = params.default_price
    if params.is_favorite is not None:
        body["is_favorite"] = params.is_favorite
    if params.conditions is not None:
        if gtype not in CONDITION_TYPES:
            raise ValueError(
                f"цель {params.goal_id} типа {gtype}: условия "
                "operator/value к ней не применимы."
            )
        _validate_conditions(gtype, params.conditions, "")
        body["conditions"] = cond_items(params.conditions)
    if params.depth is not None:
        if gtype != "number":
            raise ValueError(
                f"цель {params.goal_id} типа {gtype}: параметр depth "
                "применим только к типу number."
            )
        body["depth"] = params.depth
    if params.duration is not None:
        if gtype != "visit_duration":
            raise ValueError(
                f"цель {params.goal_id} типа {gtype}: параметр duration "
                "применим только к типу visit_duration."
            )
        body["duration"] = params.duration
    notes = _diff_notes(live_goal_body(live), body)
    if not notes:
        raise ValueError(
            f"цель {params.goal_id} уже имеет запрошенные значения — "
            "запись не требуется."
        )
    lines = [
        (
            f"Цель {params.goal_id} в счётчике Метрики {params.counter_id} "
            f"(право: {permission}) будет изменена. PUT заменяет цель "
            "целиком, непереданные поля берутся из живой цели:"
        ),
        "",
        "Будет:",
    ]
    lines += ["  " + line for line in describe_goal_body(body)]
    lines += ["", "Изменения:"] + [f"- {note}" for note in notes]
    warnings = [
        (
            "PUT заменяет цель целиком: условия, которых не будет в "
            "запросе, будут удалены (в DirectAI это делается явно — полным "
            "списком)."
        ),
        (
            "накопленная ранее статистика по цели при изменении не меняется "
            "(справка Метрики)."
        ),
    ]
    return {
        "before": {
            "counter_id": params.counter_id,
            "goal_id": params.goal_id,
            "permission": permission,
            "goal": live_goal_body(live),
        },
        "requests": [
            (
                mk.METRIKA,
                f"counter/goal/{params.goal_id}",
                {"goal": body},
            )
        ],
        "preview": "\n".join(lines),
        "warnings": warnings,
    }


async def _apply_update(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    counter_id = int(plan.params["counter_id"])
    goal_id = int(plan.params["goal_id"])
    body = plan.requests[0][2]["goal"]
    try:
        payload = await mk.put(
            ctx.require_metrika_write(),
            f"/management/v1/counter/{counter_id}/goal/{goal_id}",
            {"goal": body},
        )
    except MetrikaApiError as exc:
        if exc.unverified:
            return {
                "status": "unverified",
                "lines": unverified_lines(exc),
                "response": {"counter_id": counter_id, "goal_id": goal_id,
                             "unverified": True},
            }
        return {
            "status": "failed",
            "lines": [f"Ошибка API Метрики: {exc}"],
            "response": {"counter_id": counter_id, "goal_id": goal_id,
                         "error": str(exc)},
        }
    goal = payload.get("goal") if isinstance(payload, dict) else None
    if not isinstance(goal, dict):
        return {
            "status": "failed",
            "lines": [
                (
                    "API Метрики не вернула изменённую цель: проверьте её "
                    "через metrika_goals_list."
                )
            ],
            "response": {"counter_id": counter_id, "goal_id": goal_id},
        }
    return {
        "status": "applied",
        "lines": [f"цель {goal_id} «{goal.get('name')}» изменена."],
        "response": {"counter_id": counter_id, "goal_id": goal_id},
    }


async def _verify_update(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    counter_id = int(plan.params["counter_id"])
    goal_id = int(plan.params["goal_id"])
    body = plan.requests[0][2]["goal"]
    try:
        goals = await _goals(ctx.require_metrika_write(), counter_id)
    except ValueError as exc:
        return {"after": None, "ok": False, "note": f"read-back не удался: {exc}"}
    live = next((g for g in goals if g.get("id") == goal_id), None)
    if live is None:
        return {
            "after": {"counter_id": counter_id, "goal_id": goal_id},
            "ok": False,
            "note": f"read-back: цель {goal_id} в списке отсутствует.",
        }
    after = live_goal_body(live)
    mismatches: list[str] = []
    if str(after.get("name")) != str(body.get("name")):
        mismatches.append(
            f"имя «{after.get('name')}» вместо «{body.get('name')}»"
        )
    if goal_fingerprint(after) != goal_fingerprint(body):
        mismatches.append("тип/условия отличаются от запрошенных")
    if after.get("default_price") != body.get("default_price"):
        mismatches.append(
            f"цена {after.get('default_price')} вместо "
            f"{body.get('default_price')}"
        )
    if mismatches:
        return {
            "after": {"goal": after},
            "ok": False,
            "note": "read-back НЕ подтвердил: " + "; ".join(mismatches) + ".",
        }
    return {
        "after": {"goal": after},
        "ok": True,
        "note": (
            f"подтверждено read-back: цель {goal_id} «{after.get('name')}», "
            "имя, тип, условия и цена совпали."
        ),
    }


# --------------------------------------------------------------------------
# metrika_goal_delete
# --------------------------------------------------------------------------


async def _prepare_delete(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, MetrikaGoalDeleteParams)
    token = ctx.require_metrika_write()
    permission = await _require_editable(token, params.counter_id)
    goals = await _goals(token, params.counter_id)
    live = next((g for g in goals if g.get("id") == params.goal_id), None)
    if live is None:
        raise ValueError(
            f"цель {params.goal_id} не найдена в счётчике "
            f"{params.counter_id} — удаление отклонено до API."
        )
    name = str(live.get("name") or "")
    # Удаление необратимо в любом режиме guard: план только с owner_confirmed.
    require_owner_confirm(
        f"удаление цели Метрики {params.goal_id} («{name}») — {DELETE_DANGER}"
    )
    preview = (
        f"Цель будет удалена из счётчика Метрики {params.counter_id} "
        f"(право: {permission}):\n"
        f"- id: {params.goal_id}\n"
        f"- имя: {name}\n"
        f"- тип: {live.get('type')}\n"
        f"- целей останется: {max(0, len(goals) - 1)}\n"
        "- удаление необратимо: собранная по цели статистика пропадёт "
        "из отчётов."
    )
    return {
        "before": {
            "counter_id": params.counter_id,
            "goal_id": params.goal_id,
            "permission": permission,
            "goal": live_goal_body(live),
            "goals_total": len(goals),
        },
        "requests": [
            (
                mk.METRIKA,
                f"counter/goal/{params.goal_id}",
                {"goal_id": params.goal_id},
            )
        ],
        "preview": preview,
        "warnings": [
            (
                f"цель {params.goal_id} («{name}») удаляется навсегда: "
                "собранная по ней информация станет недоступна в отчётах."
            ),
        ],
    }


async def _apply_delete(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    counter_id = int(plan.params["counter_id"])
    goal_id = int(plan.params["goal_id"])
    name = str((plan.before or {}).get("goal", {}).get("name") or "?")
    try:
        payload = await mk.delete(
            ctx.require_metrika_write(),
            f"/management/v1/counter/{counter_id}/goal/{goal_id}",
        )
    except MetrikaApiError as exc:
        if exc.unverified:
            return {
                "status": "unverified",
                "lines": unverified_lines(exc),
                "response": {"counter_id": counter_id, "goal_id": goal_id,
                             "unverified": True},
            }
        return {
            "status": "failed",
            "lines": [f"Ошибка API Метрики: {exc}"],
            "response": {"counter_id": counter_id, "goal_id": goal_id,
                         "error": str(exc)},
        }
    if isinstance(payload, dict) and payload.get("success"):
        return {
            "status": "applied",
            "lines": [f"цель {goal_id} («{name}») удалена."],
            "response": {"counter_id": counter_id, "goal_id": goal_id,
                         "success": True},
        }
    return {
        "status": "failed",
        "lines": [
            f"удаление {goal_id}: API не подтвердило (success != true)."
        ],
        "response": {"counter_id": counter_id, "goal_id": goal_id,
                     "payload": payload},
    }


async def _verify_delete(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    counter_id = int(plan.params["counter_id"])
    goal_id = int(plan.params["goal_id"])
    try:
        goals = await _goals(ctx.require_metrika_write(), counter_id)
    except ValueError as exc:
        return {"after": None, "ok": False, "note": f"read-back не удался: {exc}"}
    left = [g for g in goals if g.get("id") == goal_id]
    if left:
        return {
            "after": {"goal": left[0]},
            "ok": False,
            "note": f"read-back НЕ подтвердил: цель {goal_id} на месте.",
        }
    return {
        "after": {"counter_id": counter_id, "goals_total": len(goals)},
        "ok": True,
        "note": (
            f"подтверждено read-back: цели {goal_id} в счётчике {counter_id} "
            f"нет (осталось целей: {len(goals)})."
        ),
    }


write_action(
    "metrika_goal_create",
    "Метрика: создать цель счётчика (JS-событие, страницы, клики, файлы, "
    "составная и др.)",
    (
        "метрика",
        "metrika",
        "создать цель",
        "добавить цель",
        "цель метрики",
        "goal create",
        "js событие",
        "составная цель",
        "мультицель",
    ),
    MetrikaGoalCreateParams,
    prepare=_prepare_create,
    apply=_apply_create,
    verify=_verify_create,
)


write_action(
    "metrika_goal_update",
    "Метрика: изменить цель (имя, условия, цена по умолчанию)",
    (
        "метрика",
        "metrika",
        "изменить цель",
        "обновить цель",
        "цель метрики",
        "goal update",
        "условия цели",
    ),
    MetrikaGoalUpdateParams,
    prepare=_prepare_update,
    apply=_apply_update,
    verify=_verify_update,
)


write_action(
    "metrika_goal_delete",
    "Метрика: удалить цель (необратимо, нужно подтверждение владельца)",
    (
        "метрика",
        "metrika",
        "удалить цель",
        "цель метрики",
        "goal delete",
        "удаление цели",
    ),
    MetrikaGoalDeleteParams,
    prepare=_prepare_delete,
    apply=_apply_delete,
    verify=_verify_delete,
)