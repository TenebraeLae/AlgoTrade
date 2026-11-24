"""Секреты бота: хранение только в OS keyring, маскирование при любом выводе.

Разрешено: получить/сохранить токен через `keyring`, дать безопасную для лога
маску `mask_token()`. Запрещено: знать о gRPC/Tinkoff/VK-протоколах, писать открытый
токен в файл/БД/лог/HTML (docs/13_SECURITY.md); это решение оператора для текущей
сессии — хранение через OS `keyring`, альтернатива AES-GCM+мастер-пароль из
docs/13_SECURITY.md не реализуется здесь (зафиксировано в CHANGELOG_AI.md).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger(__name__)

#: Имя сервиса в OS keyring. Не секрет, можно логировать.
SERVICE = "tbot"

_MIN_TOKEN_LEN = 10


class SecurityError(Exception):
    """Хранилище секретов недоступно, либо получено недопустимое (пустое) значение.

    Часть будущей иерархии `TBotError` (docs/02_CODING_RULES.md); переносится туда,
    когда появится `trading_bot/errors.py`.
    """


def mask_token(token: str) -> str:
    """Маска токена для любого лога/UI/VK: `t.xxxx…9f3a` (docs/13_SECURITY.md).

    Возврат: первые 2 символа токена (маркер контура, не секрет) + литеральная
    маска `xxxx` + последние 4 символа.
    Отказ: `ValueError`, если токен короче `_MIN_TOKEN_LEN` — короткая строка
    маскируется хуже, чем не выводится вовсе, поэтому явная ошибка честнее.
    """
    if not token or len(token) < _MIN_TOKEN_LEN:
        raise ValueError("токен слишком короткий для безопасного маскирования")
    return f"{token[:2]}xxxx\u2026{token[-4:]}"


def get_stored_token(name: str) -> str:
    """Читает уже сохранённый токен без prompt для автономного read-only сбора.

    Отсутствие keyring/секрета — явный отказ, без вывода ответа backend или токена.
    Метод не сохраняет секрет и не осуществляет сетевых вызовов.
    """
    try:
        token = keyring.get_password(SERVICE, name)
    except KeyringError as exc:
        logger.error("event=keyring_read_failed name=%s action=stop", name)
        raise SecurityError(f"keyring недоступен для {name!r}") from exc
    if not token:
        raise SecurityError(f"токен {name!r} не найден в keyring")
    return token


def get_token(name: str, *, prompt: Callable[[], str] = input) -> str:
    """Возвращает токен `name` из OS keyring; при отсутствии — запрашивает и сохраняет.

    Единственная легальная точка, где открытый токен появляется как возвращаемое
    значение. Любой лог здесь и выше обязан использовать `mask_token()`, не сам токен.

    Возврат: непустая строка токена.
    Отказ: `SecurityError`, если keyring недоступен (нет secret-service/DBus и т.п.)
    или введено пустое значение.
    """
    try:
        existing = keyring.get_password(SERVICE, name)
    except KeyringError as exc:
        raise SecurityError(f"хранилище секретов (keyring) недоступно для {name!r}: {exc}") from exc

    if existing:
        logger.debug("Токен %s найден в keyring: %s", name, mask_token(existing))
        return existing

    value = prompt().strip()
    if not value:
        raise SecurityError(f"токен {name!r} не может быть пустым")

    try:
        keyring.set_password(SERVICE, name, value)
    except KeyringError as exc:
        raise SecurityError(f"не удалось сохранить токен {name!r} в keyring: {exc}") from exc

    logger.info("Токен %s получен и сохранён в keyring: %s", name, mask_token(value))
    return value
