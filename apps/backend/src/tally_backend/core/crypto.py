"""Reversible encryption for connector pairing secrets.

Why this exists rather than a password hash: the connector authenticates with
HMAC over ``connector_id|nonce|issued_at`` keyed by the shared secret, and
verifying an HMAC requires the **same key that produced it**. A one-way hash
cannot be used for that, so storing Argon2(secret) would leave the backend
unable to authenticate anyone.

Argon2 is the right answer for *user passwords*, which are low-entropy and only
ever need comparison. It is the wrong answer for a shared machine secret, which
must be recoverable to be used. So pairing secrets are encrypted at rest with a
key that lives in the environment, not in the database. A stolen database dump
alone does not yield a single connector credential; an attacker needs the
deployment's key as well.

Key rotation: ``TALLYFLOW_SECRET_KEYS`` accepts a comma-separated list. The first
entry encrypts, and all entries are tried when decrypting, so a new key can be
rolled out ahead of re-encrypting existing rows.
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken, MultiFernet


class SecretDecryptionError(Exception):
    """No configured key could decrypt the value.

    In practice this means the encryption key changed without the stored rows
    being migrated. Every affected connector has to be re-paired, so it is worth
    failing loudly rather than reporting it as a bad password.
    """


def _derive_fernet_key(material: str) -> bytes:
    """Turn an arbitrary operator-supplied string into a valid Fernet key.

    Fernet demands exactly 32 url-safe base64 bytes, which is not something
    anyone types into a deployment manifest. SHA-256 gives a deterministic 32
    bytes from any input; the input is expected to be high-entropy already, so
    no stretching is needed or wanted here.
    """
    digest = hashlib.sha256(material.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


class SecretBox:
    """Encrypts and decrypts small secrets with authenticated encryption."""

    def __init__(self, keys: list[str]) -> None:
        if not keys:
            raise ValueError("at least one encryption key is required")
        self._fernet = MultiFernet([Fernet(_derive_fernet_key(key)) for key in keys])

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise SecretDecryptionError(
                "stored secret could not be decrypted with any configured key"
            ) from exc

    def rotate(self, ciphertext: str) -> str:
        """Re-encrypt under the current primary key without seeing plaintext."""
        return self._fernet.rotate(ciphertext.encode("ascii")).decode("ascii")
