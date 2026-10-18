"""Operator commands for the keys that seal customer books.

    python -m tally_backend.datakeys status
    python -m tally_backend.datakeys reseal
    python -m tally_backend.datakeys rewrap
    python -m tally_backend.datakeys shred <company_id> --yes

``status``  how many companies have keys, which master key wraps them, and how
            many rows are still waiting to be sealed.
``reseal``  seal every legacy plaintext row now, rather than waiting for the
            background pass the API runs at start.
``rewrap``  wrap every company key under the *current* master key. Run it after
            moving to KMS or to a new KMS key; until it has, the old master key
            must stay configured so those companies can still be opened. Fast:
            it touches one row per company, never the books themselves.
``shred``   destroy one company's books for good: its stored rows are deleted
            and its key with them, which also makes every copy of those rows in
            every backup unreadable. There is no undo -- that is the point.

Reads ``TALLYFLOW_*`` from the environment exactly as the API does, so it runs
in the API's own container: ``docker compose exec api python -m
tally_backend.datakeys status``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import delete, select

from .config import get_settings
from .core.master_keys import build_master_keys, decode_wrapped, encode_wrapped
from .db.models import Company, CompanyKey, PendingVoucher, Snapshot, VoucherRecord
from .db.session import create_engine, create_session_factory
from .services.data_keys import DataKeyring
from .services.resealing import count_plaintext, reseal_all


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    master = build_master_keys(settings)
    engine = create_engine(settings)
    factory = create_session_factory(engine, DataKeyring(master, cache_seconds=60))
    try:
        if args.command == "status":
            async with factory() as session:
                keys = (await session.execute(select(CompanyKey))).scalars().all()
                print(f"master key in use: {master.ref}")
                print(f"companies with a data key: {len({k.company_id for k in keys})}")
                for ref, count in Counter(k.master_ref for k in keys).items():
                    marker = "" if ref == master.ref else "   <- run `rewrap`"
                    print(f"  wrapped by {ref}: {count}{marker}")
                for table, count in (await count_plaintext(session)).items():
                    print(f"unsealed rows in {table}: {count}")
            return 0

        if args.command == "reseal":
            report = await reseal_all(factory, batch_size=500, pause_seconds=0)
            print(f"sealed: {report.sealed}")
            return 0

        if args.command == "rewrap":
            moved = 0
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(CompanyKey).where(CompanyKey.master_ref != master.ref)
                    )
                ).scalars().all()
                for row in rows:
                    wrapped = await master.rewrap(
                        decode_wrapped(row.wrapped_key), row.company_id, row.master_ref
                    )
                    row.wrapped_key = encode_wrapped(wrapped)
                    row.master_ref = master.ref
                    moved += 1
                await session.commit()
            print(f"rewrapped {moved} company key(s) under {master.ref}")
            return 0

        if args.command == "shred":
            if not args.yes:
                print("shred deletes a company's books and key for good; pass --yes")
                return 2
            async with factory() as session:
                company = await session.get(Company, args.company_id)
                if company is None:
                    print(f"no company {args.company_id}")
                    return 1
                for model in (Snapshot, VoucherRecord, PendingVoucher, CompanyKey):
                    await session.execute(
                        delete(model).where(model.company_id == args.company_id)
                    )
                await session.commit()
            print(
                f"shredded {company.tally_name!r} ({args.company_id}); its backups "
                "can no longer be opened either"
            )
            return 0
    finally:
        await engine.dispose()
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tally_backend.datakeys")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    sub.add_parser("reseal")
    sub.add_parser("rewrap")
    shred = sub.add_parser("shred")
    shred.add_argument("company_id")
    shred.add_argument("--yes", action="store_true")
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
