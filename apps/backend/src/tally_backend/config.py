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
    min_refresh_interval_seconds: int = 60
    refresh_worker_enabled: bool = True
    refresh_worker_interval_seconds: float = 60.0
    #: Companies refreshed per sweep. Caps the burst a single instance can put on
    #: the fleet after being idle.
    refresh_batch_size: int = 25

    # --- History sync ---------------------------------------------------
    #: A company's books are read in slices this long, newest first. Six months
    #: is the setting that decides whether a first sync works at all: asking a
    #: desktop TallyPrime for four years of vouchers in one export is what wedges
    #: it, and the shop's till is the same machine. Smaller is safer and slower.
    #:
    #: This is the lever to pull if TallyPrime starts dying with ``c0000005
    #: (Memory Access Violation)`` mid-backfill -- it crashes while *building*
    #: the collection, so the size of one slice is what decides whether it
    #: survives. Before reaching for it, note that naming leaf fields instead of
    #: whole sub-collections in ``vouchers.list`` already cut the bytes Tally
    #: has to assemble per slice by 7.6x (measured live: 6.83 MB -> 893 KB for
    #: six months), which is more headroom than halving this could buy and does
    #: not double the number of exports a shop has to sit through.
    sync_chunk_months: int = 6
    #: Ceiling on how far back a backfill reaches, even when the books start
    #: earlier. Nobody opens a phone to read a six-year-old day book, and every
    #: extra year is another multi-minute export against a live shop.
    sync_max_history_years: int = 4
    #: The ceiling on one slice, in vouchers. Calendar spans are a poor proxy
    #: for size -- a shop doing fifty vouchers a month and one doing five
    #: thousand get wildly different exports out of the same six-month window,
    #: and it is the *count* that decides whether TallyPrime survives building
    #: the collection.
    #:
    #: There is no cheap way to ask Tally how many vouchers a window holds --
    #: measured live 2026-08-29, even a date-only read costs ~1.2 KB per
    #: voucher, so a count probe over four years is itself a multi-megabyte
    #: export. So this is enforced by observation instead: a slice that comes
    #: back over the limit narrows every slice still to be read. 5000 matches
    #: the batch size the closest open-source equivalent settled on after
    #: hitting the same Tally memory limit.
    sync_chunk_max_vouchers: int = 5000
    #: Floor on an adaptive slice. Below about a week the per-export overhead
    #: dominates and a busy shop ends up with hundreds of round trips, each of
    #: which blocks its till for a moment.
    sync_chunk_min_days: int = 7
    #: Breather between slices. TallyPrime is single-threaded and shares a CPU
    #: with whoever is billing at the counter; back-to-back exports are felt.
    sync_chunk_pause_seconds: float = 3.0
    #: Per-slice budget. Generous because it is bounded work on a slow machine,
    #: and a slice that times out is retried rather than abandoned.
    sync_chunk_timeout_seconds: float = 300.0
    sync_chunk_attempts: int = 2
    #: Slices newer than this carry inventory lines; older ones do not. Stock
    #: detail is what makes a voucher export large, and no report looks at the
    #: line items on a three-year-old invoice.
    sync_inventory_days: int = 400
    #: Floor between delta syncs for one company.
    sync_delta_interval_seconds: int = 300
    #: An AlterID delta reports what changed, never what was deleted, so a
    #: recent window is re-read in full on this cadence and reconciled.
    sync_reconcile_days: int = 90
    sync_reconcile_interval_seconds: int = 24 * 3600
    #: A run whose heartbeat is older than this had its backend instance killed
    #: mid-sync. Past it the row stops being a lock and becomes resumable work.
    sync_run_stale_after_seconds: float = 600.0
    #: Whether linking a company kicks off its history backfill automatically.
    sync_auto_start: bool = True

    # --- Entitlement ceilings -------------------------------------------
    #: What the management portal pre-fills the approval form with. They are
    #: suggestions, not policy: the real ceilings live on the organisation row,
    #: agreed per customer at approval. Nothing enforces these — an unapproved
    #: organisation is capped at zero of everything by its own columns.
    default_max_companies: int = 3
    default_max_users: int = 5

    # --- Management portal ----------------------------------------------
    #: Portal sessions are a single long-lived token with no refresh family. The
    #: portal is an internal tool used from a desktop browser by a handful of
    #: people, and a refresh-rotation scheme there would be machinery guarding
    #: nothing the tenant flow does not already guard better. The platform user
    #: row is re-read on every request, so deactivating a partner takes effect
    #: immediately rather than when their token happens to expire.
    portal_token_ttl_seconds: int = 8 * 3600
    #: Seeds the first portal owner at startup when the account does not exist.
    #: Without this there is no way in: portal accounts are never self-service,
    #: so somebody has to be created out of band. The seeded account is flagged
    #: ``must_change_password`` because this value lives in a deployment
    #: manifest, which is not where a credential belongs permanently.
    portal_bootstrap_email: str = ""
    portal_bootstrap_password: str = ""

    # --- Demo account ---------------------------------------------------
    #: The shared, read-only showroom account: invented books, no connector,
    #: anyone may sign in. Off unless a deployment turns it on, because a demo
    #: that appears by default is a published login on somebody's private
    #: instance. Only the deployment that fronts the website needs one.
    demo_enabled: bool = False
    demo_email: str = "demo@tallyflow.in"
    #: Published on purpose -- the app offers a one-tap "Explore the demo" that
    #: signs in with it. Kept in configuration rather than hard-coded so it can
    #: be rotated without a release; the account is reset to this value at
    #: startup, because it belongs to the deployment and not to a person.
    demo_password: str = ""
    #: How many financial years of history the demo carries. Two gives the
    #: year-on-year comparisons something to compare against without making the
    #: first seed of a fresh database take noticeably long.
    demo_years: int = 2

    # --- Diagnostics ----------------------------------------------------
    #: Lines held in memory for the portal's live tail. Every level lands here;
    #: only WARNING and above is persisted. Five thousand is roughly an hour of
    #: a busy single instance, which is the window an incident is looked at in.
    log_ring_capacity: int = 5000
    #: Floor for what reaches the ring. DEBUG turns the portal into a firehose
    #: and is worth having for exactly one afternoon at a time.
    log_capture_level: str = "INFO"
    #: Floor for what is written to ``server_logs`` and therefore survives a
    #: redeploy. Lowering this to INFO writes a database row per request, on the
    #: same database that serves customers' reports -- do not, except briefly.
    log_persist_level: str = "WARNING"
    #: How long persisted backend logs are kept.
    log_retention_days: int = 30
    #: How long logs pushed by customers' connectors are kept. Shorter than the
    #: backend's: it is one table for the whole fleet, and a support question
    #: older than a fortnight is answered by the customer, not by a log.
    connector_log_retention_days: int = 14
    #: Whether connectors' pushed logs are accepted at all. Off makes the
    #: backend ignore the frames; connectors keep sending them and nothing
    #: breaks, which is what makes this safe to flip during an incident.
    connector_logs_enabled: bool = True
    #: Lines held in memory between database writes, across the whole fleet.
    #: Full means the newest are refused -- one flooding connector must not
    #: evict every other customer's lines.
    connector_log_buffer: int = 20_000

    # --- Releases and updates -------------------------------------------
    #: The manifest written by ``run.py publish``. Empty means "look in the usual
    #: places" (see ``services.releases.DEFAULT_MANIFEST_PATHS``), and finding
    #: nothing disables version checks rather than failing anything.
    release_manifest_path: str = ""
    #: Whether an app below the published floor is refused with 426. Off leaves
    #: the advisory headers in place but serves the request -- the switch to
    #: reach for if a bad floor ever locks the fleet out, since it restores
    #: service without needing a corrected manifest to reach every client first.
    enforce_min_app_version: bool = True
    #: Whether the backend tells outdated connectors to update over the socket.
    #: Off leaves them on their own six-hourly manifest poll.
    push_connector_updates: bool = True

    # --- Public statistics ------------------------------------------------
    #: Whether ``/v1/public/stats`` answers. It publishes four aggregate counts
    #: -- active businesses, paired PCs, companies being read, and how many of
    #: those PCs are connected right now -- for the marketing site's hero.
    #:
    #: It is a switch rather than a constant because those counts are a
    #: commercial fact about the business, readable by anyone who loads the
    #: site. Turning it off is a decision someone may want to take without a
    #: code change; the site simply stops showing the row.
    public_stats_enabled: bool = True
    #: How long a computed set of counts is reused. Every visitor to the home
    #: page asks for these, and they do not change meaningfully within a minute.
    #: Without the cache, a crawler turns a static page into four aggregate
    #: queries per hit against the same database the phones read from.
    public_stats_ttl_seconds: int = 60
    #: How recently a connector must have been heard from to count as connected.
    #: Generous relative to the heartbeat, so one dropped beat on a shop's
    #: broadband does not make the number flicker.
    public_stats_online_window_seconds: int = 300

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
