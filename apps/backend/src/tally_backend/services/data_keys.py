"""Company data keys: created on first write, unwrapped on demand, held briefly.

Every service that touches a company's stored books asks this keyring for that
company's :class:`~..core.sealing.CompanyCipher` and seals or opens with it.
The keyring rides on the database session (``session.info["keyring"]``), so
the services that already take a session need nothing new threaded through.

**A key is created in the caller's own transaction.** The first write for a
company inserts its wrapped key in the same transaction as the sealed rows that
need it, so they commit or roll back together. A key committed separately could
outlive a rolled-back write harmlessly, but the reverse -- sealed rows committed
under a key that was not -- would be books nobody can ever open again. The
unwrapped key enters the shared cache only once that transaction commits.

**Reads never create a key.** A company with no key has no sealed rows, only
legacy plaintext ones, and a cipher with no keys opens exactly those.

**Unwrapped keys are cached for ``data_key_cache_seconds``**, and a write
first checks that its cached key has not been shredded meanwhile. That makes KMS one
call per company per hour, not one per report. If KMS cannot be reached when an
entry expires, the expired key goes on being used with a warning: an AWS
blip must not turn every dashboard into an error page when the key needed is
already in memory.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.master_keys import MasterKeyError, MasterKeys, decode_wrapped, encode_wrapped
from ..core.sealing import CompanyCipher
from ..db.models import CompanyKey

logger = logging.getLogger(__name__)

_SESSION_KEY = "keyring"
_PENDING = "keyring_pending"
_HOOKED = "keyring_hooked"


@dataclass
class _Entry:
    cipher: CompanyCipher
    expires_at: float


class DataKeyring:
    def __init__(self, master: MasterKeys, *, cache_seconds: float = 3600) -> None:
        self._master = master
        self._ttl = cache_seconds
        self._cache: dict[str, _Entry] = {}

    @property
    def master(self) -> MasterKeys:
        return self._master

    async def cipher(
        self, session: AsyncSession, company_id: str, *, create: bool = False
    ) -> CompanyCipher:
        """The company's cipher. ``create`` for a write, never for a read."""
        entry = self._cache.get(company_id)
        if entry is not None and entry.expires_at > time.monotonic():
            if not create or await self._still_stored(session, entry.cipher):
                return entry.cipher
            # Shredded since it was cached (``datakeys shred`` runs in another
            # process). Sealing under it would write rows that can never be
            # opened again, so it is dropped and a new key made below.
            self.forget(company_id)
            entry = None

        pending: dict[str, CompanyCipher] = session.info.get(_PENDING, {})
        if company_id in pending:
            return pending[company_id]

        try:
            cipher = await self._load(session, company_id)
        except MasterKeyError:
            if entry is not None:
                logger.warning(
                    "could not unwrap the data key for company %s; using the cached "
                    "key past its time",
                    company_id,
                    exc_info=True,
                )
                return entry.cipher
            raise

        if cipher.can_seal:
            self._remember(cipher)
            return cipher
        if not create:
            return cipher
        return await self._create(session, company_id)

    def forget(self, company_id: str | None = None) -> None:
        """Drop cached keys -- after a shred or rewrap, so memory matches the table."""
        if company_id is None:
            self._cache.clear()
        else:
            self._cache.pop(company_id, None)

    # -- internals ---------------------------------------------------------

    @staticmethod
    async def _still_stored(session: AsyncSession, cipher: CompanyCipher) -> bool:
        # Only on the write path: one primary-key lookup beside a write.
        found = await session.scalar(
            select(CompanyKey.version).where(
                CompanyKey.company_id == cipher.company_id,
                CompanyKey.version == cipher.current,
            )
        )
        return found is not None

    async def _load(self, session: AsyncSession, company_id: str) -> CompanyCipher:
        rows = (
            await session.execute(select(CompanyKey).where(CompanyKey.company_id == company_id))
        ).scalars()
        keys: dict[int, bytes] = {}
        for row in rows:
            keys[row.version] = await self._master.unwrap(
                decode_wrapped(row.wrapped_key), company_id, row.master_ref
            )
        return CompanyCipher(company_id, keys)

    async def _create(self, session: AsyncSession, company_id: str) -> CompanyCipher:
        new = await self._master.generate(company_id)
        dialect = session.get_bind().dialect.name
        insert = pg_insert if dialect == "postgresql" else sqlite_insert
        await session.execute(
            insert(CompanyKey)
            .values(
                company_id=company_id,
                version=1,
                wrapped_key=encode_wrapped(new.wrapped),
                master_ref=new.master_ref,
            )
            # Two first writes racing for one company: whichever commits second
            # does nothing here, then reads the winner's key below. Postgres
            # holds this statement until the other transaction resolves, so the
            # re-read sees a committed row rather than nothing.
            .on_conflict_do_nothing(index_elements=["company_id", "version"])
        )
        cipher = await self._load(session, company_id)
        if not cipher.can_seal:  # pragma: no cover - the row was just written
            raise MasterKeyError(f"data key for company {company_id} vanished on creation")
        self._hold_until_commit(session, cipher)
        return cipher

    def _hold_until_commit(self, session: AsyncSession, cipher: CompanyCipher) -> None:
        session.info.setdefault(_PENDING, {})[cipher.company_id] = cipher
        if session.info.get(_HOOKED):
            return
        session.info[_HOOKED] = True
        sync = session.sync_session

        def committed(_: object) -> None:
            for held in session.info.pop(_PENDING, {}).values():
                self._remember(held)

        def rolled_back(_: object, __: object = None) -> None:
            session.info.pop(_PENDING, None)

        event.listen(sync, "after_commit", committed)
        event.listen(sync, "after_soft_rollback", rolled_back)

    def _remember(self, cipher: CompanyCipher) -> None:
        self._cache[cipher.company_id] = _Entry(cipher, time.monotonic() + self._ttl)


def keyring_of(session: AsyncSession) -> DataKeyring:
    """The keyring this session was opened with.

    Raises rather than returning ``None``: a session without a keyring could
    only write plaintext, and that is the failure this exists to make loud.
    """
    keyring = session.info.get(_SESSION_KEY)
    if keyring is None:
        raise RuntimeError(
            "this session has no data keyring; open it from the app's session factory"
        )
    return keyring


async def company_cipher(
    session: AsyncSession, company_id: str, *, create: bool = False
) -> CompanyCipher:
    return await keyring_of(session).cipher(session, company_id, create=create)
