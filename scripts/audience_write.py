"""Write-путь Аудиторий без харнеса (этап 2, ветка feat/audience-api).

Тот же путь через реестр, что у сервера: do_plan_write → человек читает план
и явно подтверждает вводом → do_apply_write. Флага автоподтверждения нет:
без точного ответа «ДА» план не применяется. Токен не печатает.

Примеры (PowerShell, каталог D:\\github\\directai-mcp):
  uv run python scripts/audience_write.py plan-from-file --file X.csv --name "[TEST DirectAI] tmp" --content-type phone
  uv run python scripts/audience_write.py plan-delete --id 7
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Callable

import directai_mcp.server as server_mod
from directai_mcp.catalog.registry import Ctx

CONFIRM_WORD = "ДА"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Аудитории: план → подтверждение → apply")
    sub = parser.add_subparsers(dest="command", required=True)
    from_file = sub.add_parser("plan-from-file", help="план создания сегмента из файла")
    from_file.add_argument("--file", required=True)
    from_file.add_argument("--name", required=True)
    from_file.add_argument("--content-type", required=True, choices=("phone", "email"))
    from_file.add_argument("--id-column", default=None)
    from_file.add_argument("--wait-timeout", type=int, default=120)
    delete = sub.add_parser("plan-delete", help="план удаления сегмента")
    delete.add_argument("--id", type=int, required=True)
    return parser.parse_args(argv)


def _ctx() -> Ctx:
    from directai_mcp.config import data_dir, get_token, load_settings

    settings = load_settings()
    return Ctx(
        settings=settings,
        token=get_token(settings.auth_login),
        data_dir=data_dir(),
    )


def _plan_params(args: argparse.Namespace) -> tuple[str, dict]:
    if args.command == "plan-from-file":
        return "audience_segment_from_file", {
            "file_path": args.file,
            "segment_name": args.name,
            "content_type": args.content_type,
            "id_column": args.id_column,
            "wait_timeout_sec": args.wait_timeout,
        }
    return "audience_segment_delete", {"segment_id": args.id}


def _extract_plan_id(plan_text: str) -> str | None:
    for line in plan_text.splitlines():
        if line.startswith("План ") and ":" in line:
            return line.split(" ", 1)[1].split(":", 1)[0].strip()
    return None


async def _flow(
    args: argparse.Namespace, input_fn: Callable[[str], str] = input
) -> int:
    name, params = _plan_params(args)
    ctx = _ctx()
    plan_text = await server_mod.do_plan_write(ctx, name, params)
    print(plan_text)
    if not plan_text.startswith("План "):
        return 1
    plan_id = _extract_plan_id(plan_text)
    if plan_id is None:
        print("Ошибка: не найден plan_id в тексте плана.", file=sys.stderr)
        return 1
    answer = input_fn(
        f"Применить план {plan_id}? Введите {CONFIRM_WORD} для подтверждения: "
    ).strip()
    if answer != CONFIRM_WORD:
        print("Не применён (нет подтверждения).")
        return 1
    print(await server_mod.do_apply_write(ctx, plan_id, True))
    return 0


def main(
    argv: list[str] | None = None,
    input_fn: Callable[[str], str] = input,
) -> int:
    args = _parse_args(argv)
    try:
        return asyncio.run(_flow(args, input_fn))
    except Exception as exc:  # noqa: BLE001 — показать любую ошибку запуска текстом
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
