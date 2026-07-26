"""Backend configuration.

Everything is environment-driven (``TALLYFLOW_*``) because the backend runs in
containers where a config file would have to be baked into the image or mounted.
The connector is the opposite -- it reads a JSON file a non-technical shop owner
received from the pairing wizard.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "staging", "prod"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TALLYFLOW_",
        env_file=".env",
        extra="ignore",
        # Without this, pydantic-settings JSON-decodes every list-typed field
        # *before* validators run, so the documented
        # `TALLYFLOW_SECRET_KEYS=key1,key2` crashes the process at boot with a
        # JSONDecodeError and `_split_list` below never sees the value. Unit
        # tests construct Settings(...) directly and never hit that path, which
        # is precisely why it stayed hidden until a real deployment.
        enable_decoding=False,
    )

    environment: Environment = "dev"
    debug: bool = False

    # --- Identity -------------------------------------------------------
    #: Signs access and refresh tokens. Generated per-process in dev so a fresh
    #: checkout just runs; refusing to start without it in prod is enforced below.
    jwt_secret: str = Field(default_factory=lambda: secrets.token_urlsafe(48))
    jwt_algorithm: str = "HS256"
    #: Short, because a stolen access token cannot be revoked -- only outlived.
    access_token_ttl_seconds: int = 15 * 60
    #: Long, but each one is stored hashed and single-use (see `services.auth`).
    refresh_token_ttl_seconds: int = 30 * 24 * 3600
    #: Encrypts connector pairing secrets at rest. Comma-separated for rotation:
    #: the first key encrypts, all keys are tried when decrypting. Defaults to
    #: the JWT secret so dev works out of the box; prod must set it explicitly,
    #: because losing this key means every connector has to be re-paired.
    secret_keys: list[str] = Field(default_factory=list)

    # --- Storage --------------------------------------------------------
    database_url: str = "sqlite+aiosqlite:///./tallyflow.db"
    db_pool_size: int = 20
    db_max_overflow: int = 10
    db_pool_timeout_seconds: float = 10.0
    #: Recycle below typical cloud-Postgres idle timeouts, which drop connections
    #: without telling the pool and produce mystery errors on the next checkout.
    db_pool_recycle_seconds: int = 1800

    # --- Horizontal scale ----------------------------------------------
    #: When set, connector job routing works across backend instances. Without
    #: it the backend still works, but only as a single process (see hub.bus).
    redis_url: str | None = None
    #: Identifies this process on the bus. Must be unique per running instance.
    instance_id: str = Field(default_factory=lambda: secrets.token_hex(6))

    # --- Connector hub --------------------------------------------------
    heartbeat_interval_seconds: float = 30.0
    #: Missing this many heartbeats marks the connector offline and frees its slot.
    heartbeat_grace_multiplier: float = 3.0
    #: TallyPrime serves one request at a time. More than a couple of concurrent
    #: jobs per connector just queues inside Tally and freezes the shop's UI.
    max_jobs_per_connector: int = 2
    default_job_timeout_seconds: float = 60.0
    heavy_job_timeout_seconds: float = 180.0

    # --- Freshness ------------------------------------------------------
    #: How old a snapshot may be before a background refresh is scheduled.
    snapshot_stale_after_seconds: int = 15 * 60
    #: Floor between forced refreshes of one company, so pull-to-refresh mashing
    #: cannot be used to hammer a shop's Tally.
    min_refresh_interval_seconds: int = 30
    refresh_worker_enabled: bool = True
    refresh_worker_interval_seconds: float = 60.0
    #: Companies refreshed per sweep. Caps the burst a single instance can put on
    #: the fleet after being idle.
    refresh_batch_size: int = 25

    # --- HTTP -----------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=list)
    #: Requests per minute per authenticated user (or per IP when anonymous).
    rate_limit_per_minute: int = 120
    #: Tighter bucket for credential endpoints, which are the ones worth guessing.
    auth_rate_limit_per_minute: int = 10

    @field_validator("jwt_secret")
    @classmethod
    def _secret_must_be_set_in_prod(cls, value: str, info: ValidationInfo) -> str:
        if info.data.get("environment") == "prod" and len(value) < 32:
            raise ValueError("TALLYFLOW_JWT_SECRET must be set to a long random value in prod")
        return value

    @field_validator("cors_origins", "secret_keys", mode="before")
    @classmethod
    def _split_list(cls, value: object) -> object:
        # Env vars arrive as a single string; a comma-separated list is friendlier
        # to write in a deployment manifest than JSON.
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def encryption_keys(self) -> list[str]:
        """Keys for connector-secret encryption, newest first."""
        return self.secret_keys or [self.jwt_secret]

    @property
    def heartbeat_grace_seconds(self) -> float:
        return self.heartbeat_interval_seconds * self.heartbeat_grace_multiplier


@lru_cache
def get_settings() -> Settings:
    return Settings()
