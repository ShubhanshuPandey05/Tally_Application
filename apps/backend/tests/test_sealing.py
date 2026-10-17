"""Customer books are sealed at rest under per-company keys.

The property that matters is not that AES works but that no path stores a copy
of somebody's books in the clear, that one company's rows never open as
another's, and that the switch-over from plaintext loses nothing.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from tally_backend.config import Settings
from tally_backend.core.master_keys import (
    AwsKmsMasterKey,
    LocalMasterKey,
    MasterKeyError,
    MasterKeys,
)
from tally_backend.core.sealing import (
    CompanyCipher,
    DataKeyMissing,
    SealedValueCorrupt,
)
from tally_backend.db.models import CompanyKey, Snapshot, VoucherRecord
from tally_backend.services.data_keys import DataKeyring, company_cipher
from tally_backend.services.resealing import count_plaintext, reseal_all
from tally_backend.services.voucher_store import PURPOSE, VoucherStore

KEY = b"k" * 32


def test_a_sealed_value_opens_only_for_its_company_and_column() -> None:
    cipher = CompanyCipher("c1", {1: KEY})
    sealed = cipher.seal({"party": "Ram & Sons", "amount": "500.00"}, purpose="voucher.payload")

    assert sealed.startswith("pa1.1.")
    assert "Ram" not in sealed
    assert cipher.open(sealed, purpose="voucher.payload") == {
        "party": "Ram & Sons",
        "amount": "500.00",
    }
    # Same key material, another company: a row copied across tenants fails.
    with pytest.raises(SealedValueCorrupt):
        CompanyCipher("c2", {1: KEY}).open(sealed, purpose="voucher.payload")
    # Same company, another column: a payload pasted into params fails.
    with pytest.raises(SealedValueCorrupt):
        cipher.open(sealed, purpose="snapshot.params")


def test_tampering_is_detected_not_decoded() -> None:
    cipher = CompanyCipher("c1", {1: KEY})
    sealed = cipher.seal([1, 2, 3], purpose="p")
    flipped = sealed[:-3] + ("A" if sealed[-3] != "A" else "B") + sealed[-2:]
    with pytest.raises(SealedValueCorrupt):
        cipher.open(flipped, purpose="p")


def test_older_key_versions_still_open_and_new_values_use_the_newest() -> None:
    old = CompanyCipher("c1", {1: KEY}).seal("then", purpose="p")
    both = CompanyCipher("c1", {1: KEY, 2: b"n" * 32})
    assert both.open(old, purpose="p") == "then"
    assert both.seal("now", purpose="p").startswith("pa1.2.")


def test_a_shredded_key_reads_as_missing_rather_than_as_nothing() -> None:
    sealed = CompanyCipher("c1", {1: KEY}).seal({"a": 1}, purpose="p")
    with pytest.raises(DataKeyMissing):
        CompanyCipher("c1", {}).open(sealed, purpose="p")
    with pytest.raises(DataKeyMissing):
        CompanyCipher("c1", {}).seal({"a": 1}, purpose="p")


def test_legacy_plaintext_passes_through_open() -> None:
    cipher = CompanyCipher("c1", {})
    assert cipher.open({"legacy": True}, purpose="p") == {"legacy": True}
    assert cipher.open("Ram & Sons", purpose="pending.party") == "Ram & Sons"
    assert cipher.open(None, purpose="p") is None


class FakeKms:
    """Just enough of KMS to show the encryption context is enforced."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.down = False
        self._wrap = LocalMasterKey(["kms-internal"])

    async def _check(self) -> None:
        if self.down:
            raise ConnectionError("KMS unreachable")

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext):  # noqa: N803
        self.calls.append("generate")
        if self.down:
            raise ConnectionError("KMS unreachable")
        plaintext = b"d" * 32
        return {"Plaintext": plaintext, "CiphertextBlob": self._blob(plaintext, EncryptionContext)}

    def encrypt(self, KeyId, Plaintext, EncryptionContext):  # noqa: N803
        self.calls.append("encrypt")
        return {"CiphertextBlob": self._blob(Plaintext, EncryptionContext)}

    def decrypt(self, CiphertextBlob, KeyId, EncryptionContext):  # noqa: N803
        self.calls.append("decrypt")
        if self.down:
            raise ConnectionError("KMS unreachable")
        context, _, plaintext = CiphertextBlob.partition(b"|")
        if context.decode() != repr(sorted(EncryptionContext.items())):
            raise ValueError("InvalidCiphertextException")
        return {"Plaintext": plaintext}

    @staticmethod
    def _blob(plaintext: bytes, context: dict[str, str]) -> bytes:
        return repr(sorted(context.items())).encode() + b"|" + plaintext


async def test_kms_binds_each_data_key_to_its_company() -> None:
    kms = FakeKms()
    master = AwsKmsMasterKey("arn:aws:kms:ap-south-1:1:key/abc", client=kms)
    new = await master.generate("company-a")

    assert new.master_ref == "kms:arn:aws:kms:ap-south-1:1:key/abc"
    assert await master.unwrap(new.wrapped, "company-a", new.master_ref) == new.plaintext
    with pytest.raises(MasterKeyError):
        await master.unwrap(new.wrapped, "company-b", new.master_ref)


async def test_moving_to_kms_keeps_reading_locally_wrapped_keys() -> None:
    local = LocalMasterKey(["old-secret"])
    before = await local.generate("c1")
    keys = MasterKeys(AwsKmsMasterKey("arn:k", client=FakeKms()), local)

    assert await keys.unwrap(before.wrapped, "c1", before.master_ref) == before.plaintext
    moved = await keys.rewrap(before.wrapped, "c1", before.master_ref)
    assert await keys.unwrap(moved, "c1", keys.ref) == before.plaintext


def test_prod_refuses_to_start_without_kms() -> None:
    base: dict[str, Any] = {
        "_env_file": None,
        "environment": "prod",
        "jwt_secret": "x" * 40,
        "secret_keys": ["k"],
    }
    with pytest.raises(ValidationError, match="DATA_KMS_KEY_ID"):
        Settings(**base)
    assert Settings(**base, data_kms_key_id="arn:k").data_kms_key_id == "arn:k"
    assert Settings(**base, allow_local_master_key=True).allow_local_master_key


# -- through the app ----------------------------------------------------------


async def _raw(app, sql: str, **params: Any) -> list[Any]:
    async with app.state.engine.connect() as conn:
        return list((await conn.execute(text(sql), params)).all())


async def test_a_dashboard_read_is_stored_sealed_and_served_in_the_clear(
    app, client, linked_company, loaded
) -> None:
    company_id = linked_company["company_id"]
    url = f"/v1/companies/{company_id}/dashboard"
    first = await client.get(url, headers=linked_company["headers"])
    assert first.status_code == 200

    rows = await _raw(
        app, "SELECT payload, params FROM snapshots WHERE company_id = :c", c=company_id
    )
    assert rows, "the dashboard must have stored snapshots"
    for payload, params in rows:
        assert payload.startswith("pa1."), "a snapshot was stored in the clear"
        assert params is None or params.startswith("pa1.")
    assert not any("Cash" in payload for payload, _ in rows)

    # Served from the sealed snapshot, the figures are the same.
    again = await client.get(url, headers=linked_company["headers"])
    figures = {k: v["data"] for k, v in first.json()["sections"].items()}
    assert {k: v["data"] for k, v in again.json()["sections"].items()} == figures
    assert any(
        v.get("meta", {}).get("from_snapshot") for v in again.json()["sections"].values()
    )

    keys = await _raw(
        app, "SELECT master_ref FROM company_keys WHERE company_id = :c", c=company_id
    )
    assert len(keys) == 1 and keys[0][0].startswith("local:")


async def test_unsealed_writes_are_refused_by_the_column(app, linked_company) -> None:
    async with app.state.session_factory() as session:
        session.add(
            Snapshot(
                company_id=linked_company["company_id"],
                dataset="x",
                params_key="k",
                payload={"in": "the clear"},
            )
        )
        with pytest.raises(Exception, match="refusing to store an unsealed value"):
            await session.flush()


async def test_a_rolled_back_first_write_leaves_no_cached_key(app, linked_company) -> None:
    company_id = linked_company["company_id"]
    keyring: DataKeyring = app.state.keyring
    async with app.state.session_factory() as session:
        cipher = await company_cipher(session, company_id, create=True)
        assert cipher.can_seal
        await session.rollback()

    async with app.state.session_factory() as session:
        stored = (
            await session.execute(select(CompanyKey).where(CompanyKey.company_id == company_id))
        ).scalars().all()
        assert stored == []
        # Not served from memory either: a key whose row rolled back would seal
        # rows nobody could open after the next restart.
        assert not (await keyring.cipher(session, company_id)).can_seal


async def test_vouchers_round_trip_through_the_store_sealed(app, linked_company) -> None:
    company_id = linked_company["company_id"]
    voucher = {
        "guid": "g-1",
        "date": "2026-10-01",
        "voucher_type": "Receipt",
        "party_name": "Ram & Sons",
        "alter_id": 7,
    }
    async with app.state.session_factory() as session:
        await VoucherStore(session).ingest(company_id, [voucher])
        await session.commit()

    (raw,) = await _raw(
        app, "SELECT payload FROM voucher_records WHERE company_id = :c", c=company_id
    )
    assert raw[0].startswith("pa1.") and "Ram" not in raw[0]

    async with app.state.session_factory() as session:
        store = VoucherStore(session)
        read = await store.read(company_id, from_date=date(2026, 10, 1), to_date=date(2026, 10, 1))
        assert read[0]["party_name"] == "Ram & Sons"
        assert (await store.find(company_id, key="g-1", on=date(2026, 10, 1)))["alter_id"] == 7


async def test_legacy_plaintext_rows_are_read_then_sealed_in_place(app, linked_company) -> None:
    company_id = linked_company["company_id"]
    async with app.state.engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO voucher_records (id, company_id, record_key, voucher_date, "
                "alter_id, payload, voucher_type, is_effective, updated_at) VALUES "
                "('legacy1', :c, 'g-old', '2026-09-01', 3, :p, 'Sales', 1, CURRENT_TIMESTAMP)"
            ),
            {"c": company_id, "p": '{"guid": "g-old", "party_name": "Old Traders"}'},
        )

    async with app.state.session_factory() as session:
        before = await VoucherStore(session).read(
            company_id, from_date=date(2026, 9, 1), to_date=date(2026, 9, 1)
        )
        assert before == [{"guid": "g-old", "party_name": "Old Traders"}]
        assert (await count_plaintext(session))["voucher_records"] == 1

    report = await reseal_all(app.state.session_factory, batch_size=1, pause_seconds=0)
    assert report.sealed["voucher_records"] == 1

    (raw,) = await _raw(app, "SELECT payload FROM voucher_records WHERE id = 'legacy1'")
    assert raw[0].startswith("pa1.")
    async with app.state.session_factory() as session:
        after = await VoucherStore(session).read(
            company_id, from_date=date(2026, 9, 1), to_date=date(2026, 9, 1)
        )
        assert after == before
        assert (await count_plaintext(session))["voucher_records"] == 0


async def test_a_shredded_company_gets_a_fresh_key_not_its_old_one(app, linked_company) -> None:
    company_id = linked_company["company_id"]
    voucher = {"guid": "g", "date": "2026-10-01", "alter_id": 1}
    async with app.state.session_factory() as session:
        await VoucherStore(session).ingest(company_id, [voucher])
        await session.commit()

    # What `datakeys shred` does, from another process: the cache here still
    # holds the old key.
    async with app.state.engine.begin() as conn:
        for table in ("voucher_records", "company_keys"):
            await conn.execute(
                text(f"DELETE FROM {table} WHERE company_id = :c"), {"c": company_id}
            )

    async with app.state.session_factory() as session:
        await VoucherStore(session).ingest(company_id, [voucher | {"alter_id": 2}])
        await session.commit()
    async with app.state.session_factory() as session:
        app.state.keyring.forget()
        rows = (await session.execute(select(VoucherRecord.payload))).scalars().all()
        cipher = await company_cipher(session, company_id)
        # Opens with the key that is in the table now -- so it will still open
        # after a restart, which a row sealed under the shredded key would not.
        assert cipher.open(rows[0], purpose=PURPOSE)["alter_id"] == 2


async def test_a_kms_outage_keeps_serving_with_the_key_already_in_memory(
    app, linked_company
) -> None:
    kms = FakeKms()
    keyring = DataKeyring(MasterKeys(AwsKmsMasterKey("arn:k", client=kms)), cache_seconds=0)
    company_id = linked_company["company_id"]
    async with app.state.session_factory() as session:
        session.info["keyring"] = keyring
        sealed = (await company_cipher(session, company_id, create=True)).seal(
            "x", purpose="p"
        )
        await session.commit()

    kms.down = True
    async with app.state.session_factory() as session:
        session.info["keyring"] = keyring
        # cache_seconds=0: the entry is already past its time.
        cipher = await company_cipher(session, company_id)
        assert cipher.open(sealed, purpose="p") == "x"
