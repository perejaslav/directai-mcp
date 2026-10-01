"""Read actions keyword_bids_forecast + phrases_forecast (B2, v1.9.0, read-only)."""

from __future__ import annotations

import asyncio
import datetime
import statistics

from pydantic import BaseModel, Field

from directai_mcp.api.errors import DirectError
from directai_mcp.api.live import LiveClient, LiveError
from directai_mcp.catalog.common import (
    GetActionParams,
    chunk,
    finalize,
    micros_to_rubles,
    net_summary,
)
from directai_mcp.catalog.registry import Ctx, action
from directai_mcp.config import AccountEntry
from directai_mcp.fmt import money

DISCLAIMER = "прогноз Яндекса, не гарантия"
MAX_FORECAST_PHRASES = 100
FORECAST_TIMEOUT = 60
FORECAST_POLL_INTERVAL = 3.0
FORECAST_CURRENCIES = ("RUB", "CHF", "EUR", "KZT", "TRY", "UAH", "USD", "BYN")
DEFAULT_TRAFFIC_VOLUMES = (5, 15, 75, 100)


def _single_entry(ctx: Ctx, account_value: str) -> tuple[AccountEntry | None, str | None]:
    try:
        entries = ctx.accounts(account_value)
    except Exception as e:  # noqa: BLE001 — ConfigError -> текст ошибки
        return None, f"Ошибка: {e}"
    if len(entries) != 1:
        return None, (
            "Ошибка: укажите ровно один кабинет (alias/логин), не 'all'/'active' "
            f"(получено кабинетов: {len(entries)})."
        )
    return entries[0], None


def _estimate_bids_units(n: int | None) -> str:
    base = "Оценка баллов: 15 за вызов KeywordBids.get + 3 за каждые 2000 фраз"
    if n is None:
        return base + " (точное число фраз станет известно после чтения)."
    per = 3 * ((n + 1999) // 2000) if n > 0 else 0
    return base + f"; для ~{n} фраз ≈ {15 + per} баллов."


def _strategy_note(strategy: dict) -> tuple[bool, str]:
    """Живой формат (10.2026): Search=SERVING_OFF + Network=AVERAGE_CPC.

    SERVING_OFF — не стратегия, а отключение показов: AuctionBids API при
    этом всё равно отдаёт (вопреки рекомендации справки не запрашивать),
    показываем справочно. Ручной поиск — HIGHEST_POSITION; ручные сети —
    MAXIMUM_COVERAGE/MANUAL_CPM.
    """
    search = (strategy.get("Search") or {}).get("BiddingStrategyType")
    network = (strategy.get("Network") or {}).get("BiddingStrategyType")
    bits: list[str] = []
    if search == "SERVING_OFF":
        bits.append("показы на поиске отключены (SERVING_OFF), данные справочные")
    elif search is not None and search != "HIGHEST_POSITION":
        bits.append(
            f"ставками на поиске управляет стратегия ({search}), прогноз справочный"
        )
    if network not in (None, "MAXIMUM_COVERAGE", "MANUAL_CPM"):
        if network in ("SERVING_OFF", "NETWORK_DEFAULT"):
            bits.append(f"показы в сетях: {network}, данные справочные")
        else:
            bits.append(
                f"ставками в сетях управляет стратегия ({network}), прогноз справочный"
            )
    if not bits:
        return False, "ручная стратегия"
    return True, "; ".join(bits)


async def _campaign_info(
    client, login: str, campaign_ids: list[int]
) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not campaign_ids:
        return out
    for part in chunk(sorted(set(campaign_ids)), 10):
        items = await client.get_all(
            "campaigns",
            {
                "SelectionCriteria": {"Ids": part},
                "FieldNames": ["Id", "Name", "Type"],
                "TextCampaignFieldNames": ["BiddingStrategy"],
                "UnifiedCampaignFieldNames": ["BiddingStrategy"],
            },
            login,
            "Campaigns",
            "v501",
        )
        for item in items:
            if not isinstance(item, dict) or item.get("Id") is None:
                continue
            try:
                cid = int(item["Id"])
            except (TypeError, ValueError):
                continue
            strategy: dict = {}
            for block in ("TextCampaign", "UnifiedCampaign"):
                body = item.get(block)
                if isinstance(body, dict):
                    strategy.update(body.get("BiddingStrategy") or {})
            out[cid] = {
                "Name": item.get("Name"),
                "Type": item.get("Type"),
                "Strategy": strategy,
            }
    return out


async def _keyword_texts(
    client, login: str, keyword_ids: list[int]
) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for part in chunk(sorted(set(keyword_ids)), 10_000):
        items = await client.get_all(
            "keywords",
            {
                "SelectionCriteria": {"Ids": part},
                "FieldNames": ["Id", "Keyword", "State", "Status", "ServingStatus"],
            },
            login,
            "Keywords",
        )
        for item in items:
            if not isinstance(item, dict) or item.get("Id") is None:
                continue
            try:
                kid = int(item["Id"])
            except (TypeError, ValueError):
                continue
            out[kid] = item
    return out


class KeywordBidsForecastParams(GetActionParams):
    account: str = Field(default="active", description="Ровно один кабинет: алиас или логин.")
    campaign_id: int | None = None
    adgroup_ids: list[int] = Field(default_factory=list)
    keyword_ids: list[int] = Field(default_factory=list)
    traffic_volumes: list[int] = Field(default_factory=lambda: [5, 15, 75, 100])
    include_network: bool = False


@action(
    "keyword_bids_forecast",
    "read",
    "Прогноз ставок по существующим фразам: объём трафика → ставка → цена (только чтение, прогноз — не гарантия)",
    ("прогноз ставок", "цена клика", "forecast", "аукцион", "auctionbids", "трафик"),
    KeywordBidsForecastParams,
)
async def _keyword_bids_forecast(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, KeywordBidsForecastParams)
    scopes = sum(
        [
            params.campaign_id is not None,
            bool(params.adgroup_ids),
            bool(params.keyword_ids),
        ]
    )
    if scopes != 1:
        return "Ошибка: укажите ровно одно из: campaign_id | adgroup_ids | keyword_ids."
    entry, err = _single_entry(ctx, params.account)
    if err is not None or entry is None:
        return err or "Ошибка: кабинет не найден."
    wanted = {int(v) for v in params.traffic_volumes} if params.traffic_volumes else set()
    search_fields = ["Bid", "AutotargetingSearchBidIsAuto", "AuctionBids"]
    network_fields = ["Bid", "Coverage"] if params.include_network else ["Bid"]
    fields = {
        "FieldNames": ["KeywordId", "AdGroupId", "CampaignId", "ServingStatus"],
        "SearchFieldNames": search_fields,
        "NetworkFieldNames": network_fields,
    }
    tally: dict = {}
    criterion: dict
    if params.campaign_id is not None:
        criterion = {"CampaignIds": [int(params.campaign_id)]}
        estimate_n: int | None = None
    elif params.adgroup_ids:
        criterion = {"AdGroupIds": list(dict.fromkeys(int(i) for i in params.adgroup_ids))}
        estimate_n = None
    else:
        ids = list(dict.fromkeys(int(i) for i in params.keyword_ids))
        if not ids:
            return "Ошибка: пустой keyword_ids."
        if len(ids) > 10_000:
            return "Ошибка: не более 10000 keyword_ids за вызов."
        criterion = {"KeywordIds": ids}
        estimate_n = len(ids)
    estimate_line = _estimate_bids_units(estimate_n)
    client = ctx.direct()
    try:
        try:
            items = await client.get_all(
                "keywordbids", dict({"SelectionCriteria": criterion}, **fields),
                entry.login, "KeywordBids", tally=tally,
            )
        except DirectError as e:
            if e.code == 152:
                rest = client.last_units.get(entry.login)
                tail = f" Остаток: {rest.rest}/{rest.limit}." if rest else ""
                return f"Ошибка: не хватает баллов API (152).{tail} {e.human_message()}"
            return f"Ошибка: {e.human_message()}"
        kid_to_text = {}
        if items:
            try:
                kid_to_text = await _keyword_texts(
                    client, entry.login,
                    [int(i["KeywordId"]) for i in items if i.get("KeywordId") is not None],
                )
            except DirectError:
                kid_to_text = {}
        camp_ids = sorted({int(i["CampaignId"]) for i in items if i.get("CampaignId") is not None})
        camps: dict[int, dict] = {}
        try:
            camps = await _campaign_info(client, entry.login, camp_ids)
        except DirectError:
            camps = {}
    finally:
        await client.aclose()
    fetched_at = datetime.datetime.now().astimezone().strftime("%d.%m.%Y %H:%M %Z")
    rows: list[dict] = []
    raw: list[dict] = []
    auto_lines: list[str] = []
    no_data = 0
    auto_managed = 0
    autotarget = 0
    prices_100: list[float] = []
    prices_15: list[float] = []
    for item in items:
        kid = item.get("KeywordId")
        gid = item.get("AdGroupId")
        cid = item.get("CampaignId")
        camp = camps.get(int(cid)) if cid is not None else None
        ctype = (camp or {}).get("Type") or "?"
        is_epk = ctype == "UNIFIED_CAMPAIGN"
        is_auto, strat_note = _strategy_note((camp or {}).get("Strategy") or {})
        if is_auto:
            auto_managed += 1
        text_item = kid_to_text.get(int(kid)) if kid is not None else None
        text = (text_item or {}).get("Keyword") if text_item else None
        is_auto_target = text == "---autotargeting"
        if is_auto_target:
            autotarget += 1
        search = item.get("Search") or {}
        auction = (search.get("AuctionBids") or {}).get("AuctionBidItems") or []
        if not auction:
            no_data += 1
        shown_items = [a for a in auction if not wanted or int(a.get("TrafficVolume", -1)) in wanted]
        if not shown_items and auction:
            shown_items = sorted(auction, key=lambda a: int(a.get("TrafficVolume", 0)))
        for a in shown_items:
            try:
                vol = int(a.get("TrafficVolume"))
            except (TypeError, ValueError):
                continue
            bid_r = micros_to_rubles(a.get("Bid"))
            price_r = micros_to_rubles(a.get("Price"))
            rows.append(
                {
                    "_account": entry.login,
                    "Keyword": text if text != "---autotargeting" else "Автотаргетинг",
                    "KeywordId": kid,
                    "AdGroupId": gid,
                    "CampaignId": cid,
                    "CampaignType": ctype,
                    "TrafficVolume": vol,
                    "Bid": money(bid_r),
                    "Price": money(price_r),
                }
            )
            if vol == 100 and price_r is not None:
                prices_100.append(float(price_r))
            if vol == 15 and price_r is not None:
                prices_15.append(float(price_r))
        raw.append(dict(item, _keyword_text=text, _campaign_type=ctype))
        status_bits = []
        if is_epk:
            status_bits.append("ЕПК")
        if is_auto:
            status_bits.append(strat_note)
        serving = item.get("ServingStatus")
        if serving == "RARELY_SERVED":
            status_bits.append("мало показов (RARELY_SERVED): AuctionBids=null")
        if text_item:
            state = text_item.get("State")
            status = text_item.get("Status")
            if state and state != "ON":
                status_bits.append(f"состояние фразы {state}")
            if status and status not in ("ACCEPTED", None):
                status_bits.append(f"статус {status}")
        if is_auto_target:
            status_bits.append("автотаргетинг: AuctionBids всегда null — строка справочная")
        if status_bits and len(auto_lines) < 20:
            auto_lines.append(f"Фраза {kid}: " + "; ".join(status_bits) + ".")

    def _med(values: list[float]) -> str:
        return money(statistics.median(values)) if values else "—"

    context = (
        f"keyword_bids_forecast @ {entry.login}. {DISCLAIMER}. "
        f"Дата получения: {fetched_at}. Валюта кабинета, НДС — как отдаёт API (без пересчёта). "
        f"{estimate_line}"
    )
    columns = [
        "Keyword", "KeywordId", "AdGroupId", "CampaignId", "CampaignType",
        "TrafficVolume", "Bid", "Price",
    ]
    display = columns
    out = finalize(
        ctx, context, "keyword_bids_forecast", display, rows,
        params.limit, params.save_as, [],
        money_cols=(), output=params.output, format=params.format,
        account=params.account,
        totals_suffix=None,
        dump_dir=params.dump_dir, dump_tag=params.dump_tag,
        dump_action="keyword_bids_forecast", dump_params=params.model_dump(),
        dump_raw={"keyword_bids_forecast": raw},
        dump_fields=fields, dump_tally=tally, dump_logins=[entry.login],
        dump_scope="campaign",
    )
    out += (
        "\n\nИтог по кампании/группе: "
        f"медиана цены при объёме 100 — {_med(prices_100)} "
        f"(фраз с данными: {len(prices_100)}); "
        f"медиана цены при объёме 15 — {_med(prices_15)} "
        f"(фраз с данными: {len(prices_15)})."
    )
    notes = [
        (
            f"Фраз прочитано: {len(items)}; без данных аукциона: {no_data}; "
            f"на стратегиях: {auto_managed}; автотаргетинг: {autotarget} (показан отдельно)."
        ),
        (
            "У автотаргетинга нет AuctionBids — в медианы он не попадает; "
            "в таблице его строк нет (нет данных аукциона)."
        ),
    ]
    if auto_lines:
        notes.append("Статусы (первые 20):\n" + "\n".join(f"- {line}" for line in auto_lines))
    out += "\n\n" + "\n".join(f"Примечание: {n}" for n in notes)
    return out


class PhrasesForecastParams(GetActionParams):
    account: str = Field(default="active", description="Ровно один кабинет: алиас или логин.")
    phrases: list[str] = Field(default_factory=list)
    region_ids: list[int] = Field(default_factory=list)
    currency: str = "RUB"
    include_auction_bids: bool = True
    forecast_id: int | None = None
    timeout_s: int = 60


@action(
    "phrases_forecast",
    "read",
    "Прогноз показов/кликов/цен для НОВЫХ фраз до добавления (Live v4; только чтение, прогноз — не гарантия)",
    ("прогноз фраз", "новые фразы", "forecast", "показы клики", "wordstat"),
    PhrasesForecastParams,
)
async def _phrases_forecast(ctx: Ctx, params: BaseModel) -> str:
    assert isinstance(params, PhrasesForecastParams)
    entry, err = _single_entry(ctx, params.account)
    if err is not None or entry is None:
        return err or "Ошибка: кабинет не найден."
    currency = (params.currency or "RUB").upper()
    if currency not in FORECAST_CURRENCIES:
        return f"Ошибка: currency только {', '.join(FORECAST_CURRENCIES)}."
    if params.timeout_s is None or params.timeout_s <= 0 or params.timeout_s > FORECAST_TIMEOUT:
        return f"Ошибка: timeout_s 1..{FORECAST_TIMEOUT} с."
    phrases = [" ".join(p.split()) for p in (params.phrases or []) if isinstance(p, str)]
    phrases = [p for p in phrases if p]
    if params.forecast_id is None:
        if not phrases:
            return "Ошибка: укажите phrases[]."
        if len(phrases) > MAX_FORECAST_PHRASES:
            return (
                f"Ошибка: не более {MAX_FORECAST_PHRASES} фраз за отчёт "
                f"(получено {len(phrases)}). Разбейте список."
            )
        if not params.region_ids:
            return "Ошибка: укажите region_ids[] (GeoID)."
    live = LiveClient(token=ctx.token)
    ctx._clients.append(live)
    fetched_at = datetime.datetime.now().astimezone().strftime("%d.%m.%Y %H:%M %Z")
    try:
        try:
            if params.forecast_id is not None:
                forecast_id = int(params.forecast_id)
            else:
                forecast_id = await live.create_new_forecast(
                    phrases, [int(r) for r in params.region_ids],
                    currency, params.include_auction_bids,
                )
        except LiveError as e:
            if str(getattr(e, "code", "")) == "31":
                return (
                    "Ошибка: на сервере уже 5 отчётов прогноза (лимит Live v4). "
                    "Удалите старые через повторный вызов после готовности "
                    "или подождите автоудаления (5 часов). " + e.human_message()
                )
            return f"Ошибка прогноза: {e.human_message()}"
        deadline = asyncio.get_event_loop().time() + float(params.timeout_s)
        status = "Pending"
        polls = 0
        while True:
            try:
                listing = await live.get_forecast_list()
            except LiveError as e:
                return f"Ошибка прогноза: {e.human_message()}"
            polls += 1
            ctx.net.polls += 1
            hit = None
            for row in listing:
                try:
                    if int(row.get("ForecastID")) == int(forecast_id):
                        hit = row
                        break
                except (TypeError, ValueError):
                    continue
            status = str((hit or {}).get("StatusForecast") or "Pending")
            if status == "Done":
                break
            if status == "Failed":
                return (
                    f"Прогноз {forecast_id}: сформировать не удалось (Failed). "
                    f"{DISCLAIMER}."
                )
            if asyncio.get_event_loop().time() >= deadline:
                ctx.net.wait_sec += float(params.timeout_s)
                out = (
                    f"Прогноз {forecast_id}: не готов за {params.timeout_s} с "
                    f"(статус {status}). Повторите вызов с forecast_id={forecast_id}. "
                    f"{DISCLAIMER}. Дата запроса: {fetched_at}."
                )
                out += "\n\n" + net_summary(ctx)
                return out
            await asyncio.sleep(FORECAST_POLL_INTERVAL)
            ctx.net.wait_sec += FORECAST_POLL_INTERVAL
        try:
            data = await live.get_forecast(forecast_id)
        except LiveError as e:
            return f"Ошибка прогноза: {e.human_message()}"
        items = data.get("Phrases") if isinstance(data, dict) else None
        common = data.get("Common") if isinstance(data, dict) else None
        if not isinstance(items, list):
            return f"Ошибка: пустой прогноз {forecast_id}."
        deleted = ""
        try:
            await live.delete_forecast_report(forecast_id)
            deleted = f"Отчёт {forecast_id} удалён (слот лимита освобождён)."
        except LiveError:
            deleted = (
                f"Отчёт {forecast_id} НЕ удалён (лимит 5 отчётов, автоудаление 5 ч)."
            )
        rows: list[dict] = []
        for phrase in items:
            if not isinstance(phrase, dict):
                continue
            auction = phrase.get("AuctionBids") if params.include_auction_bids else None
            auction_txt = "—"
            if isinstance(auction, list) and auction:
                parts = []
                for a in auction[:8]:
                    if isinstance(a, dict):
                        parts.append(
                            f"{a.get('Position')}: ставка {a.get('Bid')}, цена {a.get('Price')}"
                        )
                auction_txt = "; ".join(parts) if parts else "—"
            rows.append(
                {
                    "Phrase": phrase.get("Phrase"),
                    "Shows": phrase.get("Shows"),
                    "Clicks": phrase.get("Clicks"),
                    "FirstPlaceClicks": phrase.get("FirstPlaceClicks"),
                    "PremiumClicks": phrase.get("PremiumClicks"),
                    "CTR": phrase.get("CTR"),
                    "Min": phrase.get("Min"),
                    "Max": phrase.get("Max"),
                    "PremiumMin": phrase.get("PremiumMin"),
                    "PremiumMax": phrase.get("PremiumMax"),
                    "AuctionBids": auction_txt,
                }
            )
        total_line = "—"
        if isinstance(common, dict):
            total_line = (
                f"Показы {common.get('Shows')}, клики {common.get('Clicks')} "
                f"(1-е место {common.get('FirstPlaceClicks')}, "
                f"спецразмещение {common.get('PremiumClicks')}); "
                f"затраты: низ {common.get('Min')}, верх {common.get('Max')}, "
                f"спец {common.get('PremiumMin')} {currency}."
            )
        geo_txt = ", ".join(map(str, params.region_ids)) if params.region_ids else "все регионы"
        context = (
            f"phrases_forecast @ {entry.login} (forecast_id={forecast_id}). "
            f"{DISCLAIMER}. Дата получения: {fetched_at}. "
            f"Регионы GeoID: {geo_txt}. Валюта {currency} "
            f"(НДС — включён/нет, как отдаёт API). "
            f"Баллы API не расходуются (Live v4 без Units). {deleted}"
        )
        columns = [
            "Phrase", "Shows", "Clicks", "FirstPlaceClicks", "PremiumClicks",
            "CTR", "Min", "Max", "PremiumMin", "PremiumMax", "AuctionBids",
        ]
        out = finalize(
            ctx, context, "phrases_forecast", columns, rows,
            params.limit, params.save_as, [],
            money_cols=(), output=params.output, format=params.format,
            account=params.account,
            dump_dir=params.dump_dir, dump_tag=params.dump_tag,
            dump_action="phrases_forecast", dump_params=params.model_dump(),
            dump_raw={"phrases_forecast": items},
            dump_fields={"Currency": currency, "GeoID": params.region_ids},
            dump_tally={"pages": polls, "versions": ["live-v4"], "complete": True},
            dump_logins=[entry.login], dump_scope="account",
        )
        out += f"\n\nИтого по всем фразам: {total_line}"
        return out
    finally:
        try:
            await live.aclose()
        except Exception:  # noqa: BLE001,S110 — закрытие клиента не роняет ответ
            pass
