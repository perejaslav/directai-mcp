"""Генератор синтетических файлов для проверки конвейера Аудиторий (этап 2).

Только заведомо фиктивные значения: телефоны вида 7900000XXXX (нереальный
блок 900-000) и почты на example.com. Файлы годятся для
audience_segment_from_file (≥150 валидных строк в каждом).

Пример (PowerShell, каталог D:\\github\\directai-mcp):
  uv run python scripts/make_audience_test_file.py --out $env:TEMP\\aud-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

COUNT = 150


def _phone(i: int) -> str:
    """Фиктивный номер 7900000XXXX в разных написаниях (все валидны)."""
    suffix = f"{i:04d}"
    if i % 4 == 0:
        return f"+7 (900) 000-{suffix[:2]}-{suffix[2:]}"
    if i % 4 == 1:
        return f"8-900-000-{suffix[:2]}-{suffix[2:]}"
    if i % 4 == 2:
        return f"900000{suffix}"
    return f"7900000{suffix}"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Синтетика для audience_segment_from_file"
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.cwd(),
        help="каталог для phones.csv и emails.csv",
    )
    parser.add_argument("--count", type=int, default=COUNT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.count < 1:
        print("Ошибка: count должен быть ≥ 1.", file=sys.stderr)
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    phones = args.out / "phones.csv"
    emails = args.out / "emails.csv"
    phones.write_text(
        "phone\n" + "\n".join(_phone(i) for i in range(args.count)) + "\n",
        encoding="utf-8",
    )
    emails.write_text(
        "email\n"
        + "\n".join(f"User{i:04d}@Example.COM " for i in range(args.count))
        + "\n",
        encoding="utf-8",
    )
    print(f"phones: {phones} ({args.count} строк)")
    print(f"emails: {emails} ({args.count} строк)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
