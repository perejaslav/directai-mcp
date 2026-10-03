"""Test-campaign guard for variant B (DECISIONS step 4).

With the guard on, writes are allowed only inside campaigns named
'[TEST DirectAI]*'. Verified against live campaign names via read, never
against request params. Unknown write actions are denied by default.
Every block carries GUARD_NOTICE: it is protection, not an error.
"""

from __future__ import annotations

import contextvars
import os

from directai_mcp.api.direct import DirectClient
from directai_mcp.catalog.registry import Ctx

TEST_PREFIX = "[TEST DirectAI]"

# v1.1.34: бюджеты запрещены везде, включая [TEST DirectAI].
# Текст блокировки — по ТЗ дословно.
BUDGET_BLOCK = "Изменение бюджета запрещено политикой."
# v1.15.1: формулировки причин для режима confirm (не «запрещено»).
BUDGET_CONFIRM = "изменение бюджета или стратегии"
RENAME_BLOCK = "переименование существующей кампании запрещено при защите."
RENAME_CONFIRM = "переименование кампании"
MODERATE_BLOCK = "модерация запрещена в режиме защиты."
MODERATE_CONFIRM = "отправка на модерацию (после неё начинаются показы)"

# Запись в Аудитории выключена по умолчанию (мёрж feat/audience-api в main):
# включается только явным [audience] write_enabled=true. Текст — в стиле
# остальных блокировок guard.
AUDIENCE_WRITE_DISABLED = (
    "запись в Аудитории выключена ([audience] write_enabled=false)."
)

# v1.10.0 (Б4): запись ретаргетинга выключена по умолчанию, включается
# только явным [retargeting] write_enabled=true. Текст — в стиле остальных
# блокировок guard, с инструкцией по включению.
RETARGETING_WRITE_DISABLED = (
    "запись ретаргетинга выключена ([retargeting] write_enabled=false). "
    "Включить: секция [retargeting] write_enabled = true в конфиге."
)

RETARGETING_WRITE_ACTIONS = frozenset({
    "retargeting_list_create",
    "retargeting_list_update",
    "retargeting_list_delete",
    "audience_target_add",
    "audience_target_state",
})

# Бюджетные ключи параметров (нормализация: нижний регистр без подчеркиваний).
# Смена стратегии — тоже бюджетная операция (п.1 ТЗ): ключ strategy входит сюда.
_BUDGET_KEYS = frozenset({
    "dailybudget",
    "weeklyspendlimit",
    "weeklybudget",
    "budgettype",
    "strategy",
})

# v1.1.16: к каждой блокировке guard дописывается пояснение для агента-клиента.
# Блокировка — это защита, а не сбой: обход не предлагаем, конфиг guard не ищем.
GUARD_NOTICE = (
    "Это защита, а не ошибка. Не предлагайте обход и не ищите конфиг guard: "
    "снятие ограничения — только решением владельца вручную."
)


class GuardBlocked(Exception):
    """Blocking guard violation; plan_write must not create a plan."""

    def __init__(self, message: str) -> None:
        super().__init__(f"{message} {GUARD_NOTICE}")


# v1.15.0: режим confirm. Политические запреты (бюджет/стратегия, модерация,
# переименование, запись вне [TEST DirectAI]) не блокируют, а копятся здесь
# как «опасные» причины: план создаётся с пометкой, apply требует
# owner_confirmed=true. Жёсткими остаются: объект не найден/ошибка API,
# read_only-поля (A1), переключатели Аудиторий/ретаргетинга, неизвестные
# действия. None — режим block (политика = GuardBlocked).
_DANGER_SINK: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar(
    "directai_danger_sink", default=None
)

DANGER_NOTICE = (
    "⚠ ОПАСНАЯ ОПЕРАЦИЯ. Применять только после явного «да» владельца "
    "в чате именно на этот план: покажи причины дословно, затем "
    "apply_write с owner_confirmed=true. Без «да» — не применять."
)


def confirm_mode(ctx: Ctx) -> bool:
    return guard_active(ctx) and getattr(ctx.settings, "guard_mode", "block") == "confirm"


def start_danger_collection() -> contextvars.Token:
    return _DANGER_SINK.set([])


def finish_danger_collection(token: contextvars.Token) -> None:
    _DANGER_SINK.reset(token)


CONFIRM_SUFFIX = "нужно подтверждение владельца."


def policy(message: str, confirm: str) -> None:
    """Политический запрет: block — GuardBlocked(message);
    confirm — опасная причина «<confirm> — нужно подтверждение владельца»."""
    sink = _DANGER_SINK.get()
    if sink is None:
        raise GuardBlocked(message)
    sink.append(f"{confirm} — {CONFIRM_SUFFIX}")


def guard_active(ctx: Ctx) -> bool:
    return bool(ctx.settings.guard) or os.environ.get("DIRECTAI_TEST_GUARD") == "1"


async def _campaigns_by_id(
    client: DirectClient, login: str, ids: list[int]
) -> dict[int, dict]:
    if not ids:
        return {}
    items = await client.get_all(
        "campaigns",
        {"SelectionCriteria": {"Ids": ids}, "FieldNames": ["Id", "Name"]},
        login,
        "Campaigns",
    )
    return {int(i["Id"]): i for i in items if i.get("Id") is not None}


async def campaign_lookup(
    ctx: Ctx, client: DirectClient, login: str, campaign_id: int, days: int = 90
) -> dict:
    """B1: статус кампании для guard (Campaigns API + один лёгкий Reports).

    Возвращает словарь build_lookup. Ошибка API — failed, без трактовки.
    """
    from directai_mcp.api.errors import DirectError
    from directai_mcp.catalog import lookup as _lookup

    try:
        found = await _campaigns_by_id(client, login, [int(campaign_id)])
    except DirectError as e:
        return _lookup.failed(f"ошибка API при проверке кампании: {e.human_message()}.")
    item = found.get(int(campaign_id))
    if item is not None:
        return _lookup.resolved_configured("объект найден в Campaigns API.")
    # Campaigns API пуст — один лёгкий отчёт для statistics_only.
    try:
        from datetime import datetime, timedelta

        days_int = max(1, min(int(days), 365))
        today = datetime.now().astimezone().date()
        definition = {
            "SelectionCriteria": {
                "DateFrom": (today - timedelta(days=days_int)).isoformat(),
                "DateTo": (today - timedelta(days=1)).isoformat(),
                "Filter": [{
                    "Field": "CampaignId",
                    "Operator": "IN",
                    "Values": [str(int(campaign_id))],
                }],
            },
            "FieldNames": ["CampaignId", "Impressions", "Clicks"],
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE",
            "Format": "TSV",
            "IncludeVAT": "YES" if ctx.settings.include_vat else "NO",
        }
        reports = ctx.reports()
        try:
            _cols, rows = await reports.fetch(login, definition)
        finally:
            await reports.aclose()
        if rows:
            return _lookup.resolved_statistics_only()
        return _lookup.not_observed(f"последние {days_int} дней")
    except Exception as e:  # noqa: BLE001
        return _lookup.failed(f"проверка Reports не удалась: {e}.")


def lookup_allows_write(lookup: dict) -> bool:
    """B1: запись только при resolved+configured."""
    return (
        lookup.get("lookup_status") == "resolved"
        and lookup.get("presence") == "configured"
    )


async def _campaign_name(
    client: DirectClient, login: str, campaign_id: int
) -> str | None:
    found = await _campaigns_by_id(client, login, [campaign_id])
    item = found.get(int(campaign_id))
    return str(item["Name"]) if item and item.get("Name") else None


def _is_test(name: str | None) -> bool:
    return bool(name) and name.startswith(TEST_PREFIX)  # type: ignore[arg-type]


async def require_test_campaign(
    ctx: Ctx, client: DirectClient, login: str, campaign_id: int
) -> None:
    """B1: один Campaigns.get — и статус, и имя (без дубля запросов)."""
    from directai_mcp.api.errors import DirectError
    from directai_mcp.catalog import lookup as _lookup
    from directai_mcp.catalog.lookup import guard_message as _guard_msg

    try:
        found = await _campaigns_by_id(client, login, [int(campaign_id)])
    except DirectError as e:
        raise GuardBlocked(
            f"{_lookup.failed(f'ошибка API: {e.human_message()}.')['message']} "
            f"{GUARD_NOTICE}"
        ) from None
    item = found.get(int(campaign_id))
    if item is None:
        lookup = await campaign_lookup(ctx, client, login, campaign_id)
        # campaign_lookup повторит Campaigns.get (дешёвый и кэшируемый);
        # точный статус важнее одного запроса при отсутствии объекта.
        raise GuardBlocked(_guard_msg(lookup, campaign_id))
    name = str(item.get("Name") or "")
    if not name:
        raise GuardBlocked(f"кампания {campaign_id} не найдена в {login}.")
    if not _is_test(name):
        policy(
            f"запись в кампанию {campaign_id} («{name}») запрещена: вне тестового префикса.",
            f"запись в боевую кампанию {campaign_id} («{name}»)",
        )


async def _group_campaign(
    client: DirectClient, login: str, adgroup_id: int
) -> int | None:
    items = await client.get_all(
        "adgroups",
        {
            "SelectionCriteria": {"Ids": [adgroup_id]},
            "FieldNames": ["Id", "CampaignId"],
        },
        login,
        "AdGroups",
    )
    return items[0].get("CampaignId") if items else None


async def _keyword_group(
    client: DirectClient, login: str, keyword_id: int
) -> int | None:
    items = await client.get_all(
        "keywords",
        {"SelectionCriteria": {"Ids": [keyword_id]}, "FieldNames": ["Id", "AdGroupId"]},
        login,
        "Keywords",
    )
    return items[0].get("AdGroupId") if items else None


async def _ad_group(client: DirectClient, login: str, ad_id: int) -> int | None:
    items = await client.get_all(
        "ads",
        {"SelectionCriteria": {"Ids": [ad_id]}, "FieldNames": ["Id", "AdGroupId"]},
        login,
        "Ads",
    )
    return items[0].get("AdGroupId") if items else None


async def _require_group(
    ctx: Ctx, client: DirectClient, login: str, adgroup_id: int
) -> None:
    campaign_id = await _group_campaign(client, login, adgroup_id)
    if campaign_id is None:
        raise GuardBlocked(f"группа {adgroup_id} не найдена в {login}.")
    await require_test_campaign(ctx, client, login, int(campaign_id))


async def _require_keyword(
    ctx: Ctx, client: DirectClient, login: str, keyword_id: int
) -> None:
    group_id = await _keyword_group(client, login, keyword_id)
    if group_id is None:
        raise GuardBlocked(f"фраза {keyword_id} не найдена в {login}.")
    await _require_group(ctx, client, login, int(group_id))


async def _require_ad(ctx: Ctx, client: DirectClient, login: str, ad_id: int) -> None:
    group_id = await _ad_group(client, login, ad_id)
    if group_id is None:
        raise GuardBlocked(f"объявление {ad_id} не найдено в {login}.")
    await _require_group(ctx, client, login, int(group_id))


async def _referencing_campaigns(
    client: DirectClient, login: str, service: str, criteria: dict, items_key: str
) -> set[int]:
    """Campaign ids owning objects that reference a shared object."""
    found = await client.get_all(
        service, {"SelectionCriteria": criteria}, login, items_key
    )
    out: set[int] = set()
    for item in found:
        for key in ("CampaignId",):
            if item.get(key) is not None:
                out.add(int(item[key]))
        group_id = item.get("AdGroupId")
        if group_id is not None:
            campaign_id = await _group_campaign(client, login, int(group_id))
            if campaign_id is not None:
                out.add(int(campaign_id))
    return out


async def require_shared_exclusive(
    ctx: Ctx, client: DirectClient, login: str, kind: str, ref_id: object
) -> None:
    """Shared objects: existing ones usable only if test-campaign exclusive."""
    if kind == "shared_set":
        owned = await _shared_set_users(client, login, ref_id)
    elif kind == "sitelink_set":
        owned = await _referencing_campaigns(
            client, login, "ads", {"SitelinkSetIds": [ref_id]}, "Ads"
        )
    elif kind == "extension":
        owned = await _referencing_campaigns(
            client, login, "ads", {"AdExtensionIds": [ref_id]}, "Ads"
        )
    elif kind == "image":
        owned = await _referencing_campaigns(
            client, login, "ads", {"AdImageHashes": [ref_id]}, "Ads"
        )
    elif kind == "retargeting_list":
        owned = await _referencing_campaigns(
            client,
            login,
            "audiencetargets",
            {"RetargetingListIds": [ref_id]},
            "AudienceTargets",
        )
    else:
        raise GuardBlocked(f"неизвестный общий объект {kind}.")
    if not owned:
        return
    names = await _campaigns_by_id(client, login, sorted(owned))
    foreign = [
        f"{cid} («{names[cid].get('Name')}»)"
        for cid in sorted(owned)
        if not _is_test(names.get(cid, {}).get("Name"))
    ]
    if foreign:
        policy(
            f"объект {kind} {ref_id} используется вне тестовой кампании: "
            + ", ".join(foreign)
            + ".",
            f"общий объект {kind} {ref_id} используется в боевых кампаниях: "
            + ", ".join(foreign),
        )


async def _shared_set_users(
    client: DirectClient, login: str, ref_id: object
) -> set[int]:
    """Campaigns whose campaign/groups reference a shared negatives set."""
    owned: set[int] = set()
    campaigns = await client.get_all(
        "campaigns",
        {
            "SelectionCriteria": {},
            "FieldNames": ["Id"],
            "TextCampaignFieldNames": ["NegativeKeywordSharedSetIds"],
            "UnifiedCampaignFieldNames": ["NegativeKeywordSharedSetIds"],
        },
        login,
        "Campaigns",
    )
    for camp in campaigns:
        for key in ("TextCampaign", "UnifiedCampaign"):
            ids = (camp.get(key) or {}).get("NegativeKeywordSharedSetIds") or {}
            items = ids.get("Items") if isinstance(ids, dict) else ids
            if isinstance(items, list) and ref_id in items:
                owned.add(int(camp["Id"]))
    groups = await client.get_all(
        "adgroups",
        {
            "SelectionCriteria": {},
            "FieldNames": ["Id", "CampaignId", "NegativeKeywordSharedSetIds"],
        },
        login,
        "AdGroups",
    )
    for group in groups:
        ids = group.get("NegativeKeywordSharedSetIds") or {}
        items = ids.get("Items") if isinstance(ids, dict) else ids
        if isinstance(items, list) and ref_id in items:
            owned.add(int(group["CampaignId"]))
    return owned


async def _require_audience_test_segment(
    ctx: Ctx, segment_id: object
) -> None:
    """Удаление сегмента Аудиторий — только [TEST DirectAI]* по живому имени.

    Имя читается из API ПЕРЕД удалением; имени из параметров не доверяем.
    Так защищены и 6 реальных сегментов пользователя.
    """
    from directai_mcp.api.audience import _get
    from directai_mcp.api.errors import AudienceError
    from directai_mcp.config import get_audience_token

    try:
        segment_id_int = int(segment_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise GuardBlocked("удаление сегмента: укажите числовой segment_id.") from None
    token = get_audience_token(ctx.settings.auth_login) or ctx.token
    if not token:
        raise GuardBlocked("нет токена Аудиторий: сначала set-token --audience.")
    try:
        payload = await _get(token, "segments")
    except AudienceError as exc:
        raise GuardBlocked(f"имя сегмента не прочитано: {exc}.") from None
    items = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise GuardBlocked("нет поля `segments` в ответе GET segments.")
    found = next(
        (s for s in items if isinstance(s, dict) and s.get("id") == segment_id_int),
        None,
    )
    if found is None:
        raise GuardBlocked(f"сегмент {segment_id_int} не найден в Аудиториях.")
    name = str(found.get("name") or "")
    if not name.startswith(TEST_PREFIX):
        raise GuardBlocked(
            f"удаление сегмента {segment_id_int} («{name}») запрещено: "
            "вне тестового префикса."
        )


def _norm_key(key: str) -> str:
    return str(key).lower().replace("_", "")


def is_budget_write(action: str, params: dict) -> bool:
    """Бюджетная запись: блокируется везде, включая [TEST DirectAI]."""
    if not isinstance(params, dict):
        return False
    for key, value in params.items():
        # None/пустые значения (дефолты модели) — не запись.
        if value is None or value == {} or value == []:
            continue
        if _norm_key(key) in _BUDGET_KEYS:
            return True
    return False


def combat_allowed(action: str, params: dict) -> bool:
    """Операция разрешена в боевой кампании (с планом и read-back).

    TEST-only (False): replace-режимы, пауза кампаний/объявлений, удаления,
    всё не из списка п.2 ТЗ v1.1.34. Бюджеты тут не проверяются —
    они заблокированы везде через is_budget_write.
    """
    if not isinstance(params, dict):
        return False
    if action in ("keywords_add", "keywords_state", "ads_create", "ads_update",
                  "extensions_create", "bids_set"):
        return True
    if action == "bid_modifiers_set":
        return not params.get("delete_ids")
    if action == "negatives_set":
        return params.get("mode", "add") == "add" \
            and not params.get("update_shared_set")
    if action == "campaigns_update":
        return params.get("excluded_sites") is not None \
            and params.get("end_date") is None \
            and params.get("negatives") is None \
            and params.get("strategy") is None \
            and params.get("tracking_params") is None \
            and params.get("name") is None \
            and params.get("daily_budget") is None
    if action == "adgroups_update":
        groups = params.get("groups") or []
        if not groups:
            return False
        # v1.1.36: add/remove регионов — в боевой; replace и остальное — TEST.
        return all(
            isinstance(g, dict)
            and g.get("regions") is not None
            and g.get("regions_mode", "replace") in ("add", "remove")
            and g.get("region_ids") is None
            and g.get("name") is None
            and g.get("negatives") is None
            and g.get("tracking_params") is None
            for g in groups
        )
    return False


def precheck(action: str, params: dict) -> str | None:
    """Read-free guard rules, applied before registry lookup.

    Covers future write actions (step 5+) so bypasses block in plan_write
    even before the action is registered. Returns a hard block text; policy
    rules go through policy() (confirm mode — опасная причина, не блок).
    """
    try:
        if action == "campaigns_update" and params.get("name") is not None:
            policy(RENAME_BLOCK, RENAME_CONFIRM)
        if action == "ads_state" and params.get("operation") == "moderate":
            policy(MODERATE_BLOCK, MODERATE_CONFIRM)
        if is_budget_write(action, params):
            # v1.1.34: бюджеты запрещены везде, до обращения к API.
            # Ловит и удалённый daily_budget (сырые параметры, до валидации).
            policy(BUDGET_BLOCK, BUDGET_CONFIRM)
    except GuardBlocked as e:
        return str(e).removesuffix(f" {GUARD_NOTICE}")
    if action == "campaigns_update" and params.get("settings"):
        # A1: неуправляемое поле — до обращения к API.
        try:
            from directai_mcp.catalog.notices import is_read_only
            for s in params["settings"] or []:
                if isinstance(s, dict) and is_read_only(str(s.get("Option"))):
                    return (
                        f"поле {s.get('Option')} только читается "
                        f"(campaign_setting_notices, read_only): запись запрещена."
                    )
        except ImportError:
            pass
    return None


def _gids(params: dict) -> list[int]:
    """adgroup_ids list or single adgroup_id."""
    if params.get("adgroup_ids"):
        return [int(g) for g in params["adgroup_ids"]]
    if params.get("adgroup_id") is not None:
        return [int(params["adgroup_id"])]
    return []


async def check_write(
    ctx: Ctx, client: DirectClient | None, login: str, action: str, params: dict
) -> None:
    """Gate every write in guard mode; raises GuardBlocked. No-op outside."""
    if not guard_active(ctx):
        return
    # v1.1.34: бюджеты — запрет везде, включая [TEST DirectAI]
    # (v1.15.0: в режиме confirm — опасная причина, не блок).
    if is_budget_write(action, params):
        policy(BUDGET_BLOCK, BUDGET_CONFIRM)
    if action == "audience_segment_from_file":
        # Мёрж в main: запись в Аудитории выключена по умолчанию, включается
        # только явным [audience] write_enabled=true. Дальше — тот же guard:
        # plan_write → подтверждение человека → apply_write.
        if not ctx.settings.audience_write_enabled:
            raise GuardBlocked(AUDIENCE_WRITE_DISABLED)
        # Этап 2 (эксперимент): имя обязано нести тестовый префикс.
        name = params.get("segment_name", "")
        if not (isinstance(name, str) and name.startswith(TEST_PREFIX)):
            raise GuardBlocked(
                "создание сегмента Аудиторий без тестового префикса запрещено."
            )
        return
    if action == "audience_segment_delete":
        if not ctx.settings.audience_write_enabled:
            raise GuardBlocked(AUDIENCE_WRITE_DISABLED)
        await _require_audience_test_segment(ctx, params.get("segment_id"))
        return
    if action in RETARGETING_WRITE_ACTIONS:
        # v1.10.0 (Б4): запись ретаргетинга — только при явном
        # [retargeting] write_enabled=true. Дальше — тот же guard:
        # plan_write → подтверждение человека → apply_write; тонкие проверки
        # (использование условия, совместимость типов, префикс TEST) — в prepare.
        if not ctx.settings.retargeting_write_enabled:
            raise GuardBlocked(RETARGETING_WRITE_DISABLED)
        return
    if action == "campaigns_create":
        name = params.get("name", "")
        if not (isinstance(name, str) and name.startswith(TEST_PREFIX)):
            policy("создание кампании без тестового префикса запрещено.",
                   f"создание боевой кампании «{name}» (без префикса {TEST_PREFIX})")
        return
    if action == "campaigns_update":
        if params.get("name") is not None:
            policy(RENAME_BLOCK, RENAME_CONFIRM)
        # v1.1.34: ExcludedSites add — можно в боевой; остальное — только TEST.
        if combat_allowed(action, params):
            return
        for cid in params.get("campaign_ids", []):
            await require_test_campaign(ctx, client, login, int(cid))
        return
    if action == "campaigns_state":
        for cid in params.get("campaign_ids", []):
            await require_test_campaign(ctx, client, login, int(cid))
        return
    if action in ("adgroups_create",):
        for cid in params.get("campaign_ids", []):
            await require_test_campaign(ctx, client, login, int(cid))
        return
    if action in ("adgroups_update",):
        # v1.1.34: смена только регионов — можно в боевой.
        if combat_allowed(action, params):
            return
        for gid in _gids(params):
            await _require_group(ctx, client, login, gid)
        return
    if action in ("ads_create",):
        # v1.1.34: создание объявлений — можно в боевой.
        return
    if action in ("ads_update",):
        # v1.1.34: тексты/DisplayUrlPath/привязки — можно в боевой.
        return
    if action == "ads_state":
        if params.get("operation") == "moderate":
            policy(MODERATE_BLOCK, MODERATE_CONFIRM)
        for aid in params.get("ad_ids", []):
            await _require_ad(ctx, client, login, int(aid))
        return
    if action in ("keywords_add",):
        # v1.1.34: добавление фраз — можно в боевой.
        return
    if action in ("keywords_update", "keywords_state"):
        # v1.1.34: пауза/запуск фраз — можно в боевой; правка текста — TEST.
        if combat_allowed(action, params):
            return
        for kid in params.get("keyword_ids", []):
            await _require_keyword(ctx, client, login, int(kid))
        return
    if action == "negatives_set":
        # v1.1.34: add — можно в боевой; replace/наборы — только TEST.
        if combat_allowed(action, params):
            return
        for cid in params.get("campaign_ids", []):
            await require_test_campaign(ctx, client, login, int(cid))
        for gid in _gids(params):
            await _require_group(ctx, client, login, gid)
        shared: list = []
        update_shared = params.get("update_shared_set") or {}
        if update_shared.get("id") is not None:
            shared.append(update_shared["id"])
        for sid in shared:
            await require_shared_exclusive(ctx, client, login, "shared_set", sid)
        return
    if action == "extensions_create":
        for sid in params.get("sitelink_set_ids", []):
            await require_shared_exclusive(ctx, client, login, "sitelink_set", sid)
        for eid in params.get("extension_ids", []):
            await require_shared_exclusive(ctx, client, login, "extension", eid)
        for ref in params.get("image_hashes", []):
            await require_shared_exclusive(ctx, client, login, "image", ref)
        for lid in params.get("retargeting_list_ids", []):
            await require_shared_exclusive(ctx, client, login, "retargeting_list", lid)
        return
    if action == "bids_set":
        # v1.1.34: ставки фраз — можно в боевой.
        return
    if action == "bid_modifiers_set":
        # v1.1.34: add/set корректировок — можно в боевой; delete — TEST.
        # Delete-путь дополнительно проверяется в prepare (_own_modifier).
        if combat_allowed(action, params):
            return
        for cid in params.get("campaign_ids", []):
            await require_test_campaign(ctx, client, login, int(cid))
        for gid in params.get("adgroup_ids", []):
            await _require_group(ctx, client, login, int(gid))
        return
    if action == "offline_conversions_upload":
        return
    raise GuardBlocked(f"действие {action} недоступно в режиме защиты.")
