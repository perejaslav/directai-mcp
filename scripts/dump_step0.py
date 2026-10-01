"""Разовый вызов dump-действий v1.3.0 без харнеса (шаг 0).

Идёт через реестр действий (ACTIONS) — тот же путь, что run_read в server.py.
Установка и регистрация ветки в харнесе не нужны, только `uv run` из корня
репозитория. Токен не печатает: в вывод уходит только текст действия.
Реальные ID — только через аргументы CLI, в репозитории их нет.

Примеры (PowerShell, каталог D:\\github\\directai-mcp):
  uv run python scripts/dump_step0.py --action strategies_get --account msk
  uv run python scripts/dump_step0.py --action feeds_get --account msk
  uv run python scripts/dump_step0.py --action dynamic_targets_get --account msk --campaign-ids 900000031
  uv run python scripts/dump_step0.py --action ads_archived --account msk --campaign-ids 900000031
"""

from __future__ import annotations

import argparse
import asyncio

import directai_mcp.server  # noqa: F401 — регистрация действий в ACTIONS, как у сервера

_DUMP_ACTIONS = (
    "strategies_get",
    "feeds_get",
    "dynamic_targets_get",
    "dynamic_feed_targets_get",
    "smart_targets_get",
    "businesses_get",
    "turbopages_get",
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dump-действия v1.3.0: живой вызов через реестр ACTIONS")
    parser.add_argument(
        "--action",
        choices=(*_DUMP_ACTIONS, "ads_archived"),
        required=True,
        help="действие (ads_archived = ads_list со states=ARCHIVED)",
    )
    parser.add_argument("--account", default="active")
    parser.add_argument("--campaign-ids", type=int, nargs="*", default=[])
    parser.add_argument("--adgroup-ids", type=int, nargs="*", default=[])
    parser.add_argument("--ids", type=int, nargs="*", default=[],
                        help="strategy/feed/business/turbopage/target ids")
    parser.add_argument("--format", choices=("json", "md", "csv"),
                        default="json")
    return parser.parse_args(argv)


def _resolve_action(name: str):
    from directai_mcp.catalog.registry import ACTIONS

    act = ACTIONS.get(name)
    if act is None or act.run is None:
        available = ", ".join(sorted(ACTIONS)) or "реестр пуст"
        raise RuntimeError(
            f"действие '{name}' не найдено в реестре. Доступны: {available}."
        )
    return act


async def _run(args: argparse.Namespace) -> str:
    from directai_mcp.catalog.registry import Ctx
    from directai_mcp.config import data_dir, get_token, load_settings

    settings = load_settings()
    ctx = Ctx(
        settings=settings,
        token=get_token(settings.auth_login),
        data_dir=data_dir(),
    )
    base: dict = {"account": args.account, "format": args.format}
    if args.action == "ads_archived":
        act = _resolve_action("ads_list")
        params = act.params.model_validate(
            {**base, "campaign_ids": args.campaign_ids,
             "adgroup_ids": args.adgroup_ids, "ad_ids": args.ids,
             "states": ["ARCHIVED"]})
    elif args.action == "strategies_get":
        act = _resolve_action(args.action)
        params = act.params.model_validate(
            {**base, "strategy_ids": args.ids})
    elif args.action == "feeds_get":
        act = _resolve_action(args.action)
        params = act.params.model_validate({**base, "feed_ids": args.ids})
    elif args.action == "businesses_get":
        act = _resolve_action(args.action)
        params = act.params.model_validate(
            {**base, "business_ids": args.ids})
    elif args.action == "turbopages_get":
        act = _resolve_action(args.action)
        params = act.params.model_validate(
            {**base, "turbopage_ids": args.ids})
    else:
        act = _resolve_action(args.action)
        params = act.params.model_validate(
            {**base, "campaign_ids": args.campaign_ids,
             "adgroup_ids": args.adgroup_ids, "target_ids": args.ids})
    assert act.run is not None
    return await act.run(ctx, params)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        print(asyncio.run(_run(args)))
    except Exception as exc:  # noqa: BLE001 — показать любую ошибку запуска текстом
        print(f"ОШИБКА: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
