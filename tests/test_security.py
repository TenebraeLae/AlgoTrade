"""Тесты security.py: маскирование токена, keyring-фейк, отсутствие токена в логах."""

from __future__ import annotations

import logging

import pytest
from keyring.backend import KeyringBackend
from keyring.errors import NoKeyringError, PasswordDeleteError

from trading_bot.security import SERVICE, SecurityError, get_stored_token, get_token, mask_token


class _FakeKeyring(KeyringBackend):
    """In-memory keyring backend — реальный OS keystore в тестах не трогаем."""

    priority = 1  # type: ignore[assignment]

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self._store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self._store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        try:
            del self._store[(service, username)]
        except KeyError as exc:
            raise PasswordDeleteError("not found") from exc


class _BrokenKeyring(KeyringBackend):
    """Backend, эмулирующий отсутствие secret-service (headless Linux/Docker)."""

    priority = 1  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        raise NoKeyringError("no secret service available")

    def set_password(self, service: str, username: str, password: str) -> None:
        raise NoKeyringError("no secret service available")

    def delete_password(self, service: str, username: str) -> None:
        raise NoKeyringError("no secret service available")


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> _FakeKeyring:
    backend = _FakeKeyring()
    monkeypatch.setattr("trading_bot.security.keyring.get_password", backend.get_password)
    monkeypatch.setattr("trading_bot.security.keyring.set_password", backend.set_password)
    return backend


def test_mask_token_format() -> None:
    assert mask_token("t.abcdef9f3a") == "t.xxxx\u20269f3a"


def test_mask_token_rejects_short_token() -> None:
    with pytest.raises(ValueError):
        mask_token("short")


def test_get_token_prompts_and_stores_when_missing(fake_keyring: _FakeKeyring) -> None:
    prompt = lambda: "t.supersecrettoken9f3a"  # noqa: E731

    value = get_token("broker_token", prompt=prompt)

    assert value == "t.supersecrettoken9f3a"
    assert fake_keyring.get_password(SERVICE, "broker_token") == value


def test_get_token_does_not_prompt_when_already_stored(fake_keyring: _FakeKeyring) -> None:
    fake_keyring.set_password(SERVICE, "broker_token", "t.existingtoken9f3a")
    calls = []

    def prompt() -> str:
        calls.append(1)
        return "should-not-be-used"

    value = get_token("broker_token", prompt=prompt)

    assert value == "t.existingtoken9f3a"
    assert calls == []


def test_get_token_rejects_empty_prompt_result(fake_keyring: _FakeKeyring) -> None:
    with pytest.raises(SecurityError):
        get_token("broker_token", prompt=lambda: "   ")


def test_get_token_raises_security_error_when_keyring_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broken = _BrokenKeyring()
    monkeypatch.setattr("trading_bot.security.keyring.get_password", broken.get_password)

    with pytest.raises(SecurityError):
        get_token("broker_token", prompt=lambda: "irrelevant")


def test_autonomous_token_read_does_not_prompt_or_save(fake_keyring: _FakeKeyring) -> None:
    with pytest.raises(SecurityError, match="не найден"):
        get_stored_token("broker_token")
    fake_keyring.set_password(SERVICE, "broker_token", "t.paper-only-token")
    assert get_stored_token("broker_token") == "t.paper-only-token"


def test_autonomous_token_read_rejects_keyring_failure_without_leaking_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_service: str, _name: str) -> str:
        raise NoKeyringError("private-test-token")

    monkeypatch.setattr("trading_bot.security.keyring.get_password", broken)
    with pytest.raises(SecurityError) as error:
        get_stored_token("broker_token")
    assert "private-test-token" not in str(error.value)


def test_token_never_appears_in_logs(
    fake_keyring: _FakeKeyring,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "t.veryrealsecretvalue9f3a"
    caplog.set_level(logging.DEBUG, logger="trading_bot.security")

    get_token("broker_token", prompt=lambda: secret)
    get_token("broker_token", prompt=lambda: "unused")  # второй вызов — читает из keyring

    full_output = "\n".join(record.getMessage() for record in caplog.records)
    assert secret not in full_output
    assert mask_token(secret) in full_output
