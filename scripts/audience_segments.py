"""Разовый вызов audience_segments_* без харнеса (этап 1).

Утилита разработчика: ветка feat/audience-api влита в main (v1.4.1), действие
доступно и через MCP-сервер. Скрипт оставлен для разовых проверок вручную.

Идёт через реестр действий (ACTIONS) — тот же путь, что run_read в server.py.
Регистрация в харнесе не нужна, только `uv run` из корня репозитория.
Токен не печатает: в вывод уходит только текст действия.

Примеры (PowerShell, каталог D:\\github\\directai-mcp):
  uv run python scripts/audience_segments.py
  uv run python scripts/audience_segments.py --id 7
  uv run python scripts/audience_segments.py --format md
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import directai_mcp.server  # noqa: F401 — регистрация действий в ACTIONS, как у сервера


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Аудитории: список сегментов или один сегмент по id"
    )
    parser.add_argument(
        "--id",
        type=int,
        default=None,
        help="ID сегмента (без него — audience_segments_list)",
    )
    parser.add_argument(
        "--format",
        choices=("json", "md", "csv"),
        default="json",
        help="формат вывода действия",
    )
    return parser.parse_args(argv)


async def _run(segment_id: int | None, format: str) -> str:
    from directai_mcp.catalog.registry import Ctx
    from directai_mcp.config import data_dir, get_token, load_settings

    settings = load_settings()
    ctx = Ctx(
        settings=settings,
        token=get_token(settings.auth_login),
        data_dir=data_dir(),
    )
    if segment_id is None:
        act = _resolve_action("audience_segments_list")
        params = act.params.model_validate({"format": format})
    else:
        act = _resolve_action("audience_segment_get")
        params = act.params.model_validate(
            {"segment_id": segment_id, "format": format}
        )
    assert act.run is not None
    return await act.run(ctx, params)


def _resolve_action(name: str):
    """Действие из реестра с понятной ошибкой вместо голого KeyError."""
    from directai_mcp.catalog.registry import ACTIONS

    act = ACTIONS.get(name)
    if act is None or act.run is None:
        available = ", ".join(sorted(ACTIONS)) or "реестр пуст"
        raise RuntimeError(
            f"действие '{name}' не найдено в реестре. Доступны: {available}."
        )
    return act


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        print(asyncio.run(_run(args.id, args.format)))
    except Exception as exc:  # noqa: BLE001 — показать любую ошибку запуска текстом
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
