"""Sealing a company's books at rest.

Everything the connector reads out of a customer's Tally -- snapshots, the
voucher history, entries waiting to be sent -- is stored *sealed*: compressed,
then encrypted with AES-256-GCM under a key that belongs to that one company.
The company's key is itself stored only wrapped by a master key that never
enters the database (AWS KMS in production; see ``core.master_keys``). A dump of
the database, a backup or a disk snapshot is therefore a pile of ciphertext.

Why not end-to-end: the server *computes* every dashboard and report from these
rows, so it has to be able to read them while it does. What this buys is that
the only place a plaintext ledger exists is the memory of the API process,
for the request that needs it.

Three properties the format holds, each against a specific failure:

* **The company and the column are bound in.** Associated data is
  ``company_id|purpose``, so a row copied onto another company -- or a voucher
  payload pasted into a snapshot column -- fails to open instead of showing one
  shop's figures as another's.
* **The key version rides in the envelope.** A company's key can be replaced
  and older rows still open, so a rotation never has to be atomic.
* **Legacy plaintext still reads.** Rows written before sealing existed come
  back as the JSON they always were, and ``services.resealing`` converts them
  in the background. Nothing has to stop for the switch-over.

Compression happens before encryption because ciphertext does not compress.
Compress-then-encrypt leaks length, which matters for an interactive oracle
mixing attacker input with a secret (CRIME); a row at rest is not one.
"""

from __future__ import annotations

import base64
import json
import os
import zlib
from dataclasses import dataclass, field
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.types import Text, TypeDecorator

#: Every sealed value starts with this. Version 1: ``pa1.<key version>.<b64>``
#: where the body is a 12-byte nonce followed by the GCM ciphertext and tag of
#: zlib-compressed UTF-8 JSON.
PREFIX = "pa1."

_NONCE_BYTES = 12


class SealingError(Exception):
    """A sealed value could not be opened."""


class DataKeyMissing(SealingError):
    """The value was sealed under a company key this server does not hold.

    Either the key was deliberately destroyed (the company's data was shredded)
    or the master key that wraps it is not the one configured. Both mean the
    row is unreadable, and saying so beats a figure built from part of it.
    """


class SealedValueCorrupt(SealingError):
    """The envelope is malformed, tampered with, or bound to another company."""


def is_sealed(value: object) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


@dataclass
class CompanyCipher:
    """Seals and opens values for one company.

    Holds every version of the company's key it knows, so values written under
    an older version still open; new values always use ``current``.
    """

    company_id: str
    keys: dict[int, bytes] = field(default_factory=dict)

    @property
    def current(self) -> int | None:
        return max(self.keys) if self.keys else None

    @property
    def can_seal(self) -> bool:
        return bool(self.keys)

    def seal(self, value: Any, *, purpose: str, nullable: bool = True) -> str | None:
        """Encrypt ``value`` (anything JSON can hold).

        ``None`` stays ``None`` for a nullable column. For a NOT NULL one it is
        sealed as JSON ``null``, which is what the old JSON column stored for
        it: turning it into SQL NULL there fails the write -- and, in the
        resealer, fails the same batch on every start, forever.
        """
        if value is None and nullable:
            return None
        version = self.current
        if version is None:
            # A caller asked to seal without asking for a key to be created.
            # Writing plaintext instead would be the silent failure this whole
            # module exists to prevent.
            raise DataKeyMissing(f"no data key for company {self.company_id}")
        raw = zlib.compress(
            json.dumps(value, separators=(",", ":"), default=str).encode("utf-8"), 6
        )
        nonce = os.urandom(_NONCE_BYTES)
        body = AESGCM(self.keys[version]).encrypt(nonce, raw, self._aad(purpose))
        token = base64.urlsafe_b64encode(nonce + body).decode("ascii").rstrip("=")
        return f"{PREFIX}{version}.{token}"

    def open(self, stored: Any, *, purpose: str) -> Any:
        """The value ``stored`` holds, whether sealed or legacy plaintext."""
        if not is_sealed(stored):
            # Written before sealing existed, and decoded as plain JSON by the
            # column type. Returned as is; the resealing job will get to it.
            return stored
        try:
            version_text, token = stored[len(PREFIX) :].split(".", 1)
            version = int(version_text)
            blob = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        except (ValueError, TypeError) as exc:
            raise SealedValueCorrupt("sealed value is malformed") from exc
        key = self.keys.get(version)
        if key is None:
            raise DataKeyMissing(
                f"value sealed under key v{version} of company {self.company_id}, "
                "which this server does not hold"
            )
        if len(blob) <= _NONCE_BYTES:
            raise SealedValueCorrupt("sealed value is truncated")
        try:
            raw = AESGCM(key).decrypt(blob[:_NONCE_BYTES], blob[_NONCE_BYTES:], self._aad(purpose))
        except InvalidTag as exc:
            raise SealedValueCorrupt(
                f"sealed value failed authentication for company {self.company_id} ({purpose})"
            ) from exc
        return json.loads(zlib.decompress(raw).decode("utf-8"))

    def _aad(self, purpose: str) -> bytes:
        return f"{self.company_id}|{purpose}".encode()


class SealedJSON(TypeDecorator):
    """A column holding a sealed envelope, or legacy plaintext JSON.

    Deliberately *not* where encryption happens: a type decorator sees one value
    and never the row, so it cannot know whose key to use, and it runs
    synchronously where fetching a key from KMS would block the event loop.
    Services seal and open explicitly with a :class:`CompanyCipher`.

    What this type does do is refuse to store anything that is not sealed. That
    is the guard rail: a new code path that forgets to seal fails its first test
    rather than quietly writing a ledger in the clear.
    """

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None or is_sealed(value):
            return value
        raise TypeError(
            "refusing to store an unsealed value; seal it with the company's "
            "CompanyCipher first (see core.sealing)"
        )

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None or is_sealed(value):
            return value
        # Legacy row. Postgres JSON was converted to text by the migration, so
        # this is the JSON text it always held.
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return value

    def coerce_compared_value(self, op: Any, value: Any) -> Any:
        # `payload NOT LIKE 'pa1.%'` must compare as text; going through
        # process_bind_param would refuse the pattern as an unsealed value.
        return Text()


class SealedText(SealedJSON):
    """A short sealed string, such as a party name.

    Legacy rows hold the bare string, not JSON, so they are returned untouched.
    """

    cache_ok = True

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        return value
