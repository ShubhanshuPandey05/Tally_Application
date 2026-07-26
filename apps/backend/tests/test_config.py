"""Configuration as it is actually supplied in production: environment variables.

Every other test builds ``Settings(...)`` directly with real Python objects,
which skips the environment parsing entirely. That gap let a boot-time crash
sit undetected: ``TALLYFLOW_SECRET_KEYS=key1,key2`` -- the form documented in
`.env.example` -- was JSON-decoded by pydantic-settings before any validator
ran, so the process died with a JSONDecodeError on start. It was found by
running the server for real, not by the suite.
"""

from __future__ import annotations

import pytest

from tally_backend.config import Settings


@pytest.fixture
def env(monkeypatch):
    """A clean TALLYFLOW_* environment with no .env file interference."""
    for key in list(__import__("os").environ):
        if key.startswith("TALLYFLOW_"):
            monkeypatch.delenv(key, raising=False)

    def _set(**values: str) -> Settings:
        for name, value in values.items():
            monkeypatch.setenv(f"TALLYFLOW_{name.upper()}", value)
        return Settings(_env_file=None)

    return _set


def test_comma_separated_secret_keys_parse(env) -> None:
    settings = env(secret_keys="first-key,second-key")
    assert settings.secret_keys == ["first-key", "second-key"]
    # Order matters: the first key encrypts, all are tried for decryption.
    assert settings.encryption_keys[0] == "first-key"


def test_a_single_secret_key_is_still_a_list(env) -> None:
    assert env(secret_keys="only-key").secret_keys == ["only-key"]


def test_blank_and_padded_entries_are_dropped(env) -> None:
    settings = env(secret_keys=" first , , second ")
    assert settings.secret_keys == ["first", "second"]


def test_cors_origins_parse_the_same_way(env) -> None:
    settings = env(cors_origins="https://app.tallyflow.in,https://staging.tallyflow.in")
    assert settings.cors_origins == [
        "https://app.tallyflow.in",
        "https://staging.tallyflow.in",
    ]


def test_unset_lists_stay_empty(env) -> None:
    settings = env(jwt_secret="x" * 48)
    assert settings.secret_keys == []
    # Falls back to the JWT secret so a fresh checkout runs without ceremony.
    assert settings.encryption_keys == [settings.jwt_secret]


def test_scalars_still_come_from_the_environment(env) -> None:
    settings = env(
        environment="staging",
        max_jobs_per_connector="4",
        refresh_worker_enabled="false",
        redis_url="redis://localhost:6379/0",
    )
    assert settings.environment == "staging"
    assert settings.max_jobs_per_connector == 4
    assert settings.refresh_worker_enabled is False
    assert settings.redis_url == "redis://localhost:6379/0"


def test_prod_refuses_to_start_without_a_real_jwt_secret(env) -> None:
    with pytest.raises(ValueError, match="TALLYFLOW_JWT_SECRET"):
        env(environment="prod", jwt_secret="short")


def test_the_documented_env_file_boots(env, tmp_path) -> None:
    """Every value in `.env.example` must actually load.

    The file is what an operator copies. If a line in it cannot be parsed, the
    first thing they see of this product is a stack trace.
    """
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    filled = example.read_text(encoding="utf-8").replace(
        "TALLYFLOW_JWT_SECRET=", "TALLYFLOW_JWT_SECRET=" + "x" * 48
    ).replace(
        "TALLYFLOW_SECRET_KEYS=", "TALLYFLOW_SECRET_KEYS=key-one,key-two"
    )
    target = tmp_path / ".env"
    target.write_text(filled, encoding="utf-8")

    settings = Settings(_env_file=str(target))
    assert settings.secret_keys == ["key-one", "key-two"]
    assert settings.max_jobs_per_connector == 2
    assert settings.cors_origins == []
