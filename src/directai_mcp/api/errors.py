"""Direct API errors (SPEC 7.4-7.5).

Codes verified against
https://yandex.ru/dev/direct/doc/ref-v5/concepts/errors-list.html
"""

from __future__ import annotations

# Retry for any method, up to 3 retries, pauses 2/5/10 s.
RETRY_ANY_CODES = frozenset({52, 506})
# Retry only for read methods (get/check); writes become `unverified`.
RETRY_GET_ONLY_CODES = frozenset({1000, 1020})

CODE_NO_POINTS = 152
CODE_AUTH = 53
CODE_NOT_REGISTERED = 58
CODE_LOGIN_NOT_CONNECTED = 513

RETRY_PAUSES = (2.0, 5.0, 10.0)

READ_METHODS = frozenset({"get", "check"})


class DirectError(Exception):
    """Base error for Direct API failures. Never carries the token."""

    def __init__(
        self,
        code: int,
        message: str,
        detail: str = "",
        request_id: str = "",
        login: str = "",
    ) -> None:
        self.code = code
        self.message = message
        self.detail = detail
        self.request_id = request_id
        self.login = login
        super().__init__(self.human_message())

    def human_message(self) -> str:
        parts = [f"Direct API error {self.code}: {self.message}"]
        if self.login:
            parts.append(f"login={self.login}")
        hint = hint_for_code(self.code)
        if hint:
            parts.append(hint)
        if self.detail:
            parts.append(self.detail)
        if self.request_id:
            parts.append(f"request_id={self.request_id}")
        return ". ".join(parts)


class DirectUnverifiedError(DirectError):
    """Write failed ambiguously (timeout/network/transient).

    The operation must NOT be retried automatically; caller does read-back
    and reports status `unverified` (SPEC 3.5, 7.5).
    """


class AudienceError(Exception):
    """Этапа 1 Аудиторий: ошибка чтения API Аудиторий. Токен не несёт."""

    def __init__(self, message: str) -> None:
        super().__init__(message)


class MetrikaApiError(Exception):
    """Метрика Management API: ошибка запроса. Токен не несёт.

    ``unverified=True`` — обрыв связи/таймаут на записи: результат неизвестен,
    повторять вслепую нельзя, нужен read-back.
    """

    def __init__(
        self, message: str, status: int = 0, unverified: bool = False
    ) -> None:
        self.status = status
        self.unverified = unverified
        super().__init__(message)


#: v1.17.0: подсказка про metrika:write. Токен не печатается и не читается —
#: только инструкция, как перевыпустить токен с нужным правом.
METRIKA_WRITE_SCOPE_HINT = (
    "у токена нет права metrika:write (создание/изменение/удаление целей "
    "Метрики). Перевыпустите токен в приложении Метрики (доступ «Яндекс "
    "Метрика», права metrika:read и metrika:write), затем у себя в "
    "терминале выполните `directai-mcp set-metrika-token --login "
    "<ваш логин>` и вставьте новый токен в скрытое поле. Токен в чат не "
    "пишите."
)


def metrika_hint(status: int) -> str:
    if status == 401:
        return (
            "токен не принят Метрикой — перевыпустите: "
            "`directai-mcp set-metrika-token --login <ваш логин>`"
        )
    if status == 403:
        return METRIKA_WRITE_SCOPE_HINT
    if status == 404:
        return "счётчик или цель не найдены (проверьте counter_id / goal_id)"
    if status == 409:
        return "конфликт состояния цели"
    return ""


def audience_hint(status: int) -> str:
    if status == 401:
        return "токен не принят — перевыпустите: directai-mcp set-token --audience"
    if status == 403:
        return "у приложения/токена нет прав Аудиторий (создание и чтение сегментов)"
    if status == 404:
        return "ресурс не найден (проверьте id сегмента)"
    return ""


def hint_for_code(code: int) -> str:
    if code == CODE_AUTH:
        return "check token: run `directai-mcp set-token`"
    if code == CODE_NOT_REGISTERED:
        return "finish app registration in Direct web interface"
    if code == CODE_NO_POINTS:
        return "out of API points, wait for daily reset"
    if code == CODE_LOGIN_NOT_CONNECTED:
        return "login has no Direct account"
    return ""


def from_response(payload: dict, login: str = "") -> DirectError:
    """Build DirectError from a response body containing `error`."""
    err = payload.get("error", {}) if isinstance(payload, dict) else {}
    return DirectError(
        code=int(err.get("error_code", -1)),
        message=str(err.get("error_string", "unknown error")),
        detail=str(err.get("error_detail", "")),
        request_id=str(err.get("request_id", "")),
        login=login,
    )
