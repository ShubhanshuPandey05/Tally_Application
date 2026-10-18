"""The master key that wraps every company's data key.

The master key is the one secret that turns a database dump back into books,
so it never enters the database and, in production, never enters this process
either: with AWS KMS the key material stays inside KMS, and this server can
only ask KMS to wrap or unwrap a company key -- an ask that is authenticated by
the machine's IAM role, logged in CloudTrail, and revocable in one place.

Two implementations behind one small interface:

* :class:`AwsKmsMasterKey` -- production. ``GenerateDataKey`` makes a fresh
  AES-256 key and returns it both plain and wrapped; ``Decrypt`` unwraps.
  The encryption context names the company, so KMS itself refuses to unwrap
  one company's key as another's, and CloudTrail records whose books were
  opened.
* :class:`LocalMasterKey` -- development, tests, and a self-hosted box with no
  AWS account. AES-256-GCM under a key derived from a deployment secret.
  Production refuses to start on it unless explicitly told to (see
  ``config.Settings``).

boto3 is synchronous, so every call runs in a worker thread: a KMS round trip
on the event loop would stall every connector socket this process holds.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import Settings

#: Bound into every wrap, alongside the company id.
_APP_CONTEXT = "prime-analyst/company-data-key"


class MasterKeyError(Exception):
    """The master key could not wrap or unwrap a company key."""


@dataclass(frozen=True)
class NewDataKey:
    plaintext: bytes
    wrapped: bytes
    master_ref: str


class MasterKey(Protocol):
    #: Which master key wrapped a row -- stored beside it, so a rotation can
    #: find exactly the rows still wrapped by the old one.
    ref: str

    async def generate(self, company_id: str) -> NewDataKey: ...

    async def unwrap(self, wrapped: bytes, company_id: str, master_ref: str) -> bytes: ...

    async def wrap(self, plaintext: bytes, company_id: str) -> bytes: ...


def _context(company_id: str) -> dict[str, str]:
    return {"app": _APP_CONTEXT, "company_id": company_id}


class AwsKmsMasterKey:
    """A KMS symmetric key. ``key_id`` may be an ARN, key id or alias ARN."""

    def __init__(self, key_id: str, *, region: str | None = None, client: Any = None) -> None:
        self._key_id = key_id
        self.ref = f"kms:{key_id}"
        if client is None:
            try:
                import boto3  # imported here so dev and tests need no AWS SDK
            except ImportError as exc:  # pragma: no cover - packaging error
                raise MasterKeyError("boto3 is required for TALLYFLOW_DATA_KMS_KEY_ID") from exc
            client = boto3.client("kms", region_name=region) if region else boto3.client("kms")
        self._client = client

    async def generate(self, company_id: str) -> NewDataKey:
        try:
            response = await asyncio.to_thread(
                self._client.generate_data_key,
                KeyId=self._key_id,
                KeySpec="AES_256",
                EncryptionContext=_context(company_id),
            )
        except Exception as exc:
            raise MasterKeyError(f"KMS GenerateDataKey failed: {exc}") from exc
        return NewDataKey(
            plaintext=response["Plaintext"],
            wrapped=response["CiphertextBlob"],
            master_ref=self.ref,
        )

    async def unwrap(self, wrapped: bytes, company_id: str, master_ref: str) -> bytes:
        # The KMS key id is taken from master_ref, not from settings: a row
        # wrapped before a move to a new KMS key must still unwrap under the
        # old one until it is rewrapped.
        key_id = master_ref[4:] if master_ref.startswith("kms:") else self._key_id
        try:
            response = await asyncio.to_thread(
                self._client.decrypt,
                CiphertextBlob=wrapped,
                KeyId=key_id,
                EncryptionContext=_context(company_id),
            )
        except Exception as exc:
            raise MasterKeyError(f"KMS Decrypt failed: {exc}") from exc
        return response["Plaintext"]


    async def wrap(self, plaintext: bytes, company_id: str) -> bytes:
        """Wrap a key that exists already -- used when moving off a local key."""
        try:
            response = await asyncio.to_thread(
                self._client.encrypt,
                KeyId=self._key_id,
                Plaintext=plaintext,
                EncryptionContext=_context(company_id),
            )
        except Exception as exc:
            raise MasterKeyError(f"KMS Encrypt failed: {exc}") from exc
        return response["CiphertextBlob"]


def _derive(material: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=_APP_CONTEXT.encode()
    ).derive(material.encode("utf-8"))


def _fingerprint(key: bytes) -> str:
    return hashlib.sha256(b"fingerprint|" + key).hexdigest()[:12]


class LocalMasterKey:
    """AES-256-GCM key wrapping under secrets from the environment.

    Several secrets may be configured for rotation: the first wraps, and a row
    is unwrapped by the one whose fingerprint it carries.
    """

    def __init__(self, secrets: list[str]) -> None:
        if not secrets:
            raise ValueError("at least one master secret is required")
        self._keys = {_fingerprint(k): k for k in (_derive(s) for s in secrets)}
        primary = _derive(secrets[0])
        self._primary = primary
        self.ref = f"local:{_fingerprint(primary)}"

    async def generate(self, company_id: str) -> NewDataKey:
        plaintext = AESGCM.generate_key(bit_length=256)
        return NewDataKey(plaintext, await self.wrap(plaintext, company_id), self.ref)

    async def wrap(self, plaintext: bytes, company_id: str) -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self._primary).encrypt(nonce, plaintext, self._aad(company_id))

    async def unwrap(self, wrapped: bytes, company_id: str, master_ref: str) -> bytes:
        fingerprint = master_ref.split(":", 1)[-1]
        key = self._keys.get(fingerprint)
        if key is None:
            raise MasterKeyError(f"no configured master secret matches {master_ref}")
        try:
            return AESGCM(key).decrypt(wrapped[:12], wrapped[12:], self._aad(company_id))
        except InvalidTag as exc:
            raise MasterKeyError(f"data key for {company_id} failed to unwrap") from exc

    @staticmethod
    def _aad(company_id: str) -> bytes:
        return f"{_APP_CONTEXT}|{company_id}".encode()


class MasterKeys:
    """The configured master key, plus any older ones rows may still name.

    New data keys are always wrapped by ``primary``. Unwrapping is routed by the
    ``master_ref`` stored beside each row, so a deployment that moves from a
    local key to KMS -- or from one KMS key to another -- keeps reading every
    company until ``datakeys rewrap`` has moved them all.
    """

    def __init__(self, primary: MasterKey, *others: MasterKey) -> None:
        self.primary = primary
        self._all = (primary, *others)

    @property
    def ref(self) -> str:
        return self.primary.ref

    async def generate(self, company_id: str) -> NewDataKey:
        return await self.primary.generate(company_id)

    async def unwrap(self, wrapped: bytes, company_id: str, master_ref: str) -> bytes:
        kind = master_ref.split(":", 1)[0]
        for key in self._all:
            if key.ref.split(":", 1)[0] == kind:
                return await key.unwrap(wrapped, company_id, master_ref)
        raise MasterKeyError(f"no configured master key can unwrap {master_ref}")

    async def rewrap(self, wrapped: bytes, company_id: str, master_ref: str) -> bytes:
        """The same data key, wrapped by the primary master key instead."""
        plaintext = await self.unwrap(wrapped, company_id, master_ref)
        return await self.primary.wrap(plaintext, company_id)


def build_master_keys(settings: Settings) -> MasterKeys:
    """The master keys this deployment is configured for.

    With KMS configured, KMS is primary and any local secrets are kept only to
    read data keys wrapped before the move. Without it, the local secret is
    primary -- which production refuses unless told otherwise (see config).
    """
    if settings.data_kms_key_id:
        kms = AwsKmsMasterKey(settings.data_kms_key_id, region=settings.data_kms_region)
        return MasterKeys(kms, LocalMasterKey(settings.data_master_secrets))
    return MasterKeys(LocalMasterKey(settings.data_master_secrets))


def encode_wrapped(blob: bytes) -> str:
    return base64.b64encode(blob).decode("ascii")


def decode_wrapped(text: str) -> bytes:
    return base64.b64decode(text)
