"""Write-действия API Яндекс Аудиторий, этап 2 (экспериментально).

Два действия, только через plan_write → apply_write:
- `audience_segment_from_file` — сегмент uploading из локального CSV/TXT:
  нормализация + SHA256 локально, в API уходят только хеши;
- `audience_segment_delete` — удаление сегмента, только `[TEST DirectAI]*`
  по живому имени (читается ПЕРЕД удалением, имени из параметров не доверяем).

Кабинета у действий нет (сегменты — владельца токена): в планах account_login
равен [auth] login, см. ветку do_plan_write. Сырые контакты и хеши не попадают
в preview/before/requests/журнал/логи/ошибки — только метаданные (путь,
sha256 файла, счётчики, content_type, имя, id, статус).

Confirm всегда content_type "crm": phone/email — поля CRM-формата
(«в записи должно быть хотя бы одно из полей phone или email»).

Docs: https://yandex.ru/dev/audience/ru/intro/data-requirements (форматы,
нормализация phone/email, минимум 100),
https://yandex.ru/dev/audience/ru/ref/openapi/segments/uploadCsvFile ,
https://yandex.ru/dev/audience/ru/ref/openapi/segments/confirm ,
https://yandex.ru/dev/audience/ru/ref/openapi/segments/delete .
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import os
import re
import tempfile
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from directai_mcp.api.audience import _delete, _get, _post_file, _post_json
from directai_mcp.api.errors import AudienceError
from directai_mcp.catalog.registry import Ctx, write_action
from directai_mcp.config import AccountEntry
from directai_mcp.safety.guard import TEST_PREFIX, GuardBlocked

# Ограничение экспериментальной ветки: имена сегментов только с префиксом.
TEST_SEGMENT_PREFIX = TEST_PREFIX

# Минимум записей по доке uploadFile/uploadCsvFile.
MIN_RECORDS = 100

# Терминальные статусы обработки сегмента.
DONE_STATUSES = ("processed", "few_data", "processing_failed")

CONSENT_WARNING = (
    "в Яндекс уйдут только SHA256-хеши нормализованных контактов "
    "(сырые значения не передаются); убедитесь, что есть согласие субъектов "
    "на обработку (152-ФЗ)"
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

_KNOWN_COLUMNS = frozenset({
    "phone", "email", "external_id", "ext_id",
    "idfa_gaid", "mac", "client_id",
})


def _token(ctx: Ctx) -> str:
    """Токен Аудиторий: отдельный или основной (как в этапе 1)."""
    from directai_mcp.config import get_audience_token

    resolved = get_audience_token(ctx.settings.auth_login) or ctx.token
    if not resolved:
        raise AudienceError(
            "нет токена Аудиторий: выполните `directai-mcp set-token --audience` "
            "или задайте DIRECTAI_AUDIENCE_TOKEN"
        )
    return resolved


def _sha256_hex(text: str) -> str:
    """SHA256 hex нормализованного значения (UTF-8)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _norm_phone(raw: str) -> str | None:
    """Телефон → 7XXXXXXXXXX. Доки: цифры с кодом страны, без пробелов и символов.

    Правила 8→7 и 10 цифр→7 — по ТЗ этапа (в доках только требование формата).
    """
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        return "7" + digits
    if len(digits) == 11 and digits.startswith("8"):
        return "7" + digits[1:]
    if len(digits) == 11 and digits.startswith("7"):
        return digits
    return None


def _norm_email(raw: str) -> str | None:
    """Email → trim + lower. Доки: латиница, @ и домен, без прописных."""
    text = (raw or "").strip().lower()
    if not text or not text.isascii() or not _EMAIL_RE.match(text):
        return None
    return text


def _normalize(content_type: str, raw: str) -> str | None:
    if content_type == "phone":
        return _norm_phone(raw)
    return _norm_email(raw)


def _read_text_rows(file_path: str) -> tuple[list[list[str]], str]:
    """Байты файла → строки CSV. XLSX отклоняем сразу с понятным текстом."""
    from pathlib import Path

    suffix = Path(file_path).suffix.lower()
    if suffix in (".xls", ".xlsx", ".xlsm"):
        raise ValueError(
            f"файл {file_path}: XLSX не поддерживается — сохраните как CSV."
        )
    try:
        raw = Path(file_path).read_bytes()
    except OSError as exc:
        raise ValueError(f"файл {file_path}: {exc}.") from None
    text: str | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1251"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ValueError(f"файл {file_path}: неизвестная кодировка.")
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=[",", ";", "\t", "|"])
        delimiter = dialect.delimiter
    except csv.Error:
        delimiter = ","
    rows = [row for row in csv.reader(text.splitlines(), delimiter=delimiter)]
    return rows, delimiter


def _pick_column(
    rows: list[list[str]], content_type: str, id_column: str | None
) -> tuple[int, int]:
    """Выбор колонки контактов → (index, header_rows_to_skip)."""
    if not rows or not any(any(cell.strip() for cell in row) for row in rows):
        raise ValueError("файл пуст: нет ни одной записи.")
    first = rows[0]
    header = {
        cell.strip().lower() for cell in first if cell.strip()
    } & _KNOWN_COLUMNS
    skip = 1 if header else 0
    width = max(len(row) for row in rows)
    if id_column is not None:
        key = id_column.strip()
        if key.isdigit():
            index = int(key)
            if not 0 <= index < width:
                raise ValueError(
                    f"колонка {id_column}: вне диапазона (в файле {width})."
                )
            return index, skip
        lowered = [cell.strip().lower() for cell in first]
        if key.lower() in lowered:
            return lowered.index(key.lower()), 1 if not header else skip
        raise ValueError(f"колонка {id_column}: не найдена в файле.")
    if width == 1:
        return 0, skip
    # Колонок несколько и выбор не указан: не гадаем, какая из них контакты
    # (ошиблись колонкой — захешировали чужое). Имя/номер обязательны.
    raise ValueError(
        "в файле несколько колонок: укажите id_column (имя или номер с 0)."
    )


def _load_hashes(
    file_path: str, content_type: str, id_column: str | None
) -> dict[str, Any]:
    """Локальная обработка файла → хеши и счётчики. Ничего не отправляет."""
    rows, _ = _read_text_rows(file_path)
    index, skip = _pick_column(rows, content_type, id_column)
    read = valid = invalid = 0
    seen: set[str] = set()
    duplicates = 0
    for row in rows[skip:]:
        if index >= len(row):
            continue
        cell = row[index].strip()
        if not cell:
            continue
        read += 1
        normed = _normalize(content_type, cell)
        if normed is None:
            invalid += 1
            continue
        digest = _sha256_hex(normed)
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)
        valid += 1
    return {
        "read": read,
        "valid": valid,
        "invalid": invalid,
        "duplicates": duplicates,
        "total": len(seen),
        "hashes": sorted(seen),
    }


def _sha256_file(file_path: str) -> str:
    """SHA256 hex сырых байт файла (метаданные для плана)."""
    from pathlib import Path

    digest = hashlib.sha256()
    with Path(file_path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _segments(token: str) -> list[dict]:
    payload = await _get(token, "segments")
    items = payload.get("segments") if isinstance(payload, dict) else None
    if items is None:
        raise AudienceError("нет поля `segments` в ответе GET segments")
    return [s for s in items if isinstance(s, dict)]


def _find(items: list[dict], segment_id: int) -> dict | None:
    for seg in items:
        if seg.get("id") == segment_id:
            return seg
    return None


def _size(seg: dict) -> str | int:
    for key in ("matched_quantity", "item_quantity"):
        value = seg.get(key)
        if isinstance(value, int):
            return value
    return "—"


class AudienceFromFileParams(BaseModel):
    file_path: str = Field(min_length=1, description="Локальный CSV/TXT с контактами")
    segment_name: str = Field(min_length=1, description="Имя сегмента (пока только [TEST DirectAI]*)")
    content_type: Literal["phone", "email"] = Field(description="Колонка контактов")
    id_column: str | None = Field(
        default=None,
        description="Имя или номер колонки с 0 (если в файле их несколько)",
    )
    wait_timeout_sec: int = Field(
        default=120, ge=0, le=1800, description="Поллинг статуса: 0 — не ждать"
    )


def _check_test_name(segment_name: str) -> None:
    if not segment_name.startswith(TEST_SEGMENT_PREFIX):
        raise GuardBlocked(
            "создание сегмента Аудиторий без тестового префикса "
            f"{TEST_SEGMENT_PREFIX} запрещено (эксперимент)."
        )


async def _prepare_from_file(
    ctx: Ctx, entry: AccountEntry, params: BaseModel
) -> dict:
    assert isinstance(params, AudienceFromFileParams)
    _check_test_name(params.segment_name)
    try:
        result = await asyncio.to_thread(
            _load_hashes, params.file_path, params.content_type, params.id_column
        )
        file_sha = await asyncio.to_thread(_sha256_file, params.file_path)
    except ValueError as exc:
        raise ValueError(str(exc)) from None
    if result["total"] < MIN_RECORDS:
        raise ValueError(
            f"валидных записей {result['total']}: нужно не менее {MIN_RECORDS} "
            "(требование uploadFile)."
        )
    preview = (
        "Будет создан сегмент Аудиторий:\n"
        f"- файл: {params.file_path}\n"
        f"- sha256 файла: {file_sha}\n"
        f"- прочитано: {result['read']}, валидно: {result['valid']}, "
        f"невалидно: {result['invalid']}, дублей: {result['duplicates']}, "
        f"итог: {result['total']}\n"
        f"- content_type: {params.content_type}\n"
        f"- имя сегмента: {params.segment_name}\n"
        f"- предупреждение: {CONSENT_WARNING}."
    )
    return {
        "before": {
            "file_path": params.file_path,
            "file_sha256": file_sha,
            "content_type": params.content_type,
            "segment_name": params.segment_name,
            "read": result["read"],
            "valid": result["valid"],
            "invalid": result["invalid"],
            "duplicates": result["duplicates"],
            "total": result["total"],
        },
        "requests": [
            (
                "audience",
                "segments/upload_csv_file",
                {
                    "name": params.segment_name,
                    "content_type": params.content_type,
                },
            ),
            (
                "audience",
                "segment/confirm",
                {
                    "name": params.segment_name,
                    "content_type": "crm",
                    "hashed": True,
                    "hashing_alg": "SHA256",
                },
            ),
        ],
        "preview": preview,
        "warnings": [CONSENT_WARNING + "."],
    }


def _remove_quietly(path: str | None) -> None:
    """Удалить временный файл с хешами; отсутствие — не ошибка."""
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


async def _upload_hashes(token: str, content: bytes) -> int:
    """CSV с хешами → upload_csv_file. Возвращает id загруженного сегмента."""
    payload = await _post_file(
        token, "segments/upload_csv_file", "data.csv", content
    )
    segment = payload.get("segment") if isinstance(payload, dict) else None
    segment_id = (segment or {}).get("id") if isinstance(segment, dict) else None
    if segment_id is None:
        raise AudienceError("нет id сегмента в ответе upload_csv_file")
    return int(segment_id)


async def _confirm_segment(token: str, segment_id: int, name: str) -> dict:
    payload = await _post_json(
        token,
        f"segment/{segment_id}/confirm",
        {
            "segment": {
                "name": name,
                "hashed": True,
                "hashing_alg": "SHA256",
                "content_type": "crm",
            }
        },
    )
    segment = payload.get("segment") if isinstance(payload, dict) else None
    if not isinstance(segment, dict):
        raise AudienceError("нет `segment` в ответе confirm")
    return segment


async def _poll_status(
    token: str, segment_id: int, timeout_sec: int
) -> tuple[dict | None, int]:
    """Поллинг GET segments до терминального статуса. Возвращает (сегмент, polls).

    Timeout — не ошибка: возвращаем последний виденный сегмент (или None).
    """
    delay = 2.0
    deadline = time.monotonic() + timeout_sec
    polls = 0
    last: dict | None = None
    while True:
        items = await _segments(token)
        last = _find(items, segment_id)
        if last is None:
            raise AudienceError(f"сегмент {segment_id} пропал из списка")
        if last.get("status") in DONE_STATUSES:
            return last, polls
        if timeout_sec <= 0 or time.monotonic() >= deadline:
            return last, polls
        await asyncio.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        polls += 1
        delay = min(delay * 1.5, 15.0)


async def _apply_from_file(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    params = plan.params
    token = _token(ctx)
    try:
        result = await asyncio.to_thread(
            _load_hashes,
            params["file_path"],
            params["content_type"],
            params.get("id_column"),
        )
    except ValueError as exc:
        return {"status": "failed", "lines": [f"файл: {exc}"], "response": None}
    if result["total"] < MIN_RECORDS:
        return {
            "status": "failed",
            "lines": [f"валидных записей {result['total']}: нужно {MIN_RECORDS}."],
            "response": None,
        }
    # TOCTOU: файл могли подменить между планом и apply — сверяем с тем,
    # что подтвердил человек (только метаданные: sha256 и счётчик).
    before = plan.before or {}
    try:
        current_sha = await asyncio.to_thread(_sha256_file, params["file_path"])
    except OSError as exc:
        return {"status": "failed", "lines": [f"файл: {exc}."], "response": None}
    if current_sha != before.get("file_sha256"):
        return {
            "status": "failed",
            "lines": [("файл изменён после построения плана, "
                       "постройте план заново.")],
            "response": None,
        }
    if result["total"] != before.get("total"):
        return {
            "status": "failed",
            "lines": [(f"счётчик записей изменился после построения плана "
                       f"({before.get('total')} → {result['total']}), "
                       "постройте план заново.")],
            "response": None,
        }
    tmp_name: str | None = None
    try:
        # Файл живёт после close (загрузка + Windows-лок), удаление в finally.
        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            encoding="utf-8",
            suffix=".csv",
            prefix="directai-aud-",
            dir=tempfile.gettempdir(),
            delete=False,
        )
        try:
            handle.write(params["content_type"] + "\n")
            handle.write("\n".join(result["hashes"]) + "\n")
        finally:
            handle.close()
        tmp_name = handle.name
        from pathlib import Path

        content = Path(tmp_name).read_bytes()
        try:
            upload_id = await _upload_hashes(token, content)
        finally:
            content = b""
        segment = None
        try:
            segment = await _confirm_segment(
                token, upload_id, params["segment_name"]
            )
        except AudienceError as exc:
            # Сирота: загрузка создана, но не подтверждена. Не удаляем
            # автоматически и не трогаем delete-guard — только id и подсказка.
            return {
                "status": "failed",
                "lines": [
                    f"Ошибка API Аудиторий: {exc}",
                    (
                        f"загрузка {upload_id} осталась неподтверждённой "
                        "(сегмент создан, но не сохранён): проверьте её через "
                        "audience_segment_get или удалите вручную в интерфейсе "
                        "Аудиторий."
                    ),
                ],
                "response": {"segment_id": upload_id, "confirmed": False},
            }
        segment_id = int(segment.get("id") or upload_id)
        if params.get("wait_timeout_sec", 120):
            final, _ = await _poll_status(
                token, segment_id, int(params.get("wait_timeout_sec", 120))
            )
        else:
            items = await _segments(token)
            final = _find(items, segment_id)
        if final is None:
            raise AudienceError(f"сегмент {segment_id} пропал из списка")
        status = str(final.get("status") or "?")
        size = _size(final)
        response = {"segment_id": segment_id, "status": status, "size": size}
        if status == "processing_failed":
            return {
                "status": "partial",
                "lines": [
                    (
                        f"сегмент {segment_id} создан, но обработка не удалась "
                        "(processing_failed)."
                    )
                ],
                "response": response,
            }
        lines = [f"сегмент {segment_id}: статус {status}."]
        if size != "—":
            lines.append(f"размер: {size}.")
        return {"status": "applied", "lines": lines, "response": response}
    except AudienceError as exc:
        return {
            "status": "failed",
            "lines": [f"Ошибка API Аудиторий: {exc}"],
            "response": None,
        }
    finally:
        _remove_quietly(tmp_name)


async def _verify_from_file(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    last = getattr(plan, "last_response", None) or {}
    response = last.get("response") or {}
    segment_id = response.get("segment_id")
    if segment_id is None:
        return {"after": None, "ok": False, "note": "read-back: нет id сегмента."}
    try:
        items = await _segments(_token(ctx))
    except AudienceError as exc:
        return {"after": None, "ok": False, "note": f"read-back не удался: {exc}"}
    found = _find(items, int(segment_id))
    if found is None:
        return {
            "after": None,
            "ok": False,
            "note": f"read-back НЕ подтвердил: сегмент {segment_id} отсутствует.",
        }
    status = str(found.get("status") or "?")
    if status in DONE_STATUSES:
        note = f"подтверждено read-back: сегмент {segment_id}, статус {status}."
        if status == "few_data":
            note += " few_data на синтетике — нормально для проверки конвейера."
        return {"after": found.get("id"), "ok": True, "note": note}
    return {
        "after": found.get("id"),
        "ok": False,
        "note": f"сегмент {segment_id} ещё обрабатывается (статус {status}): "
        "проверьте позже через audience_segment_get.",
    }


write_action(
    "audience_segment_from_file",
    "Аудитории: создать сегмент из файла контактов (только хеши SHA256)",
    (
        "аудитории",
        "audience",
        "сегмент из файла",
        "загрузить базу",
        "crm",
        "телефоны",
        "email",
        "хеши",
        "создать сегмент",
    ),
    AudienceFromFileParams,
    prepare=_prepare_from_file,
    apply=_apply_from_file,
    verify=_verify_from_file,
)


class AudienceSegmentDeleteParams(BaseModel):
    segment_id: int = Field(description="ID сегмента (удаление — только [TEST DirectAI]*)")


async def _prepare_delete(ctx: Ctx, entry: AccountEntry, params: BaseModel) -> dict:
    assert isinstance(params, AudienceSegmentDeleteParams)
    try:
        items = await _segments(_token(ctx))
    except AudienceError as exc:
        raise ValueError(f"Аудитории: {exc}") from None
    found = _find(items, params.segment_id)
    if found is None:
        raise ValueError(f"сегмент {params.segment_id} не найден в Аудиториях.")
    name = str(found.get("name") or "")
    if not name.startswith(TEST_SEGMENT_PREFIX):
        raise GuardBlocked(
            f"удаление сегмента {params.segment_id} («{name}») запрещено: "
            "вне тестового префикса."
        )
    return {
        "before": {"segment_id": params.segment_id, "name": name},
        "requests": [("audience", "segment/delete", {"id": params.segment_id})],
        "preview": (
            "Будет удалён сегмент Аудиторий:\n"
            f"- id: {params.segment_id}\n"
            f"- имя: {name}\n"
            "- удаление необратимо; только тестовый префикс."
        ),
        "warnings": [f"сегмент {params.segment_id} («{name}») будет удалён навсегда."],
    }


async def _apply_delete(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    segment_id = int(plan.params["segment_id"])
    name = str((plan.before or {}).get("name") or "?")
    try:
        payload = await _delete(_token(ctx), f"segment/{segment_id}")
    except AudienceError as exc:
        return {
            "status": "failed",
            "lines": [f"Ошибка API Аудиторий: {exc}"],
            "response": None,
        }
    if isinstance(payload, dict) and payload.get("success"):
        return {
            "status": "applied",
            "lines": [f"сегмент {segment_id} («{name}») удалён."],
            "response": {"segment_id": segment_id, "success": True},
        }
    return {
        "status": "failed",
        "lines": [f"удаление {segment_id}: API не подтвердило (success != true)."],
        "response": {"segment_id": segment_id},
    }


async def _verify_delete(ctx: Ctx, entry: AccountEntry, plan) -> dict:
    segment_id = int(plan.params["segment_id"])
    try:
        items = await _segments(_token(ctx))
    except AudienceError as exc:
        return {"after": None, "ok": False, "note": f"read-back не удался: {exc}"}
    if _find(items, segment_id) is None:
        return {
            "after": None,
            "ok": True,
            "note": f"подтверждено read-back: сегмент {segment_id} отсутствует.",
        }
    return {
        "after": segment_id,
        "ok": False,
        "note": f"read-back НЕ подтвердил: сегмент {segment_id} на месте.",
    }


write_action(
    "audience_segment_delete",
    "Аудитории: удалить сегмент (только [TEST DirectAI]*)",
    (
        "аудитории",
        "audience",
        "удалить сегмент",
        "delete segment",
        "удаление",
    ),
    AudienceSegmentDeleteParams,
    prepare=_prepare_delete,
    apply=_apply_delete,
    verify=_verify_delete,
)
