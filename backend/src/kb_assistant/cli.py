"""Operator commands.

    python -m kb_assistant.cli generate-signing-key --out .secrets/jwt_signing_key.pem
    python -m kb_assistant.cli seed-demo
    python -m kb_assistant.cli purge-expired-messages --older-than-days 365
    python -m kb_assistant.cli audit-heads

`seed-demo` connects as the schema owner (`KB_DATABASE__MIGRATION_URL`) because
creating tenants is a platform operation the API role is not allowed to do.
The demo password comes from `KB_SEED_PASSWORD`; there is no default.
"""

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from kb_assistant.application.auth import check_password_policy
from kb_assistant.domain.ids import new_id
from kb_assistant.infrastructure.db.tables import (
    DepartmentRow,
    MembershipDepartmentRow,
    MembershipRow,
    TenantRow,
    UserRow,
)
from kb_assistant.infrastructure.security.passwords import Argon2PasswordHasher
from kb_assistant.maintenance import audit_heads, purge_expired_messages

RSA_KEY_BITS = 3072


def generate_signing_key(out: Path) -> None:
    if out.exists():
        raise SystemExit(f"{out} already exists; refusing to overwrite a signing key")
    out.parent.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_BITS)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    # Created with owner-only permissions from the first byte.
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    print(f"wrote {out} ({RSA_KEY_BITS}-bit RSA, mode 600)")


# --------------------------------------------------------------------------- demo data


@dataclass(frozen=True)
class DemoMember:
    email: str
    tenant: str
    role: str
    departments: tuple[str, ...]


DEMO_TENANTS = {"acme": "Acme Corp", "globex": "Globex"}
DEMO_DEPARTMENTS = {"engineering": "Engineering", "hr": "Human Resources", "product": "Product"}
DEMO_MEMBERS = (
    DemoMember("alice@acme.example", "acme", "tenant_admin", ()),
    DemoMember("bob@acme.example", "acme", "dept_admin", ("engineering",)),
    DemoMember("carol@acme.example", "acme", "employee", ("hr",)),
    DemoMember("dave@acme.example", "acme", "employee", ("engineering", "product")),
    DemoMember("erin@globex.example", "globex", "tenant_admin", ()),
    DemoMember("frank@globex.example", "globex", "employee", ("engineering",)),
    # A consultant in both tenants: the hardest case for isolation tests (ADR-0012).
    DemoMember("grace@consulting.example", "acme", "employee", ("engineering",)),
    DemoMember("grace@consulting.example", "globex", "dept_admin", ("product",)),
)


async def _seed(conn: AsyncConnection, password_hash: str) -> None:
    tenants: dict[str, UUID] = {}
    departments: dict[tuple[str, str], UUID] = {}
    for slug, name in DEMO_TENANTS.items():
        existing = await conn.scalar(select(TenantRow.id).where(TenantRow.slug == slug))
        if existing is not None:
            print(f"tenant {slug!r} already exists, skipping the demo seed")
            return
        tenants[slug] = new_id()
        await conn.execute(insert(TenantRow).values(id=tenants[slug], name=name, slug=slug))
        for dept_slug, dept_name in DEMO_DEPARTMENTS.items():
            departments[(slug, dept_slug)] = new_id()
            await conn.execute(
                insert(DepartmentRow).values(
                    id=departments[(slug, dept_slug)],
                    tenant_id=tenants[slug],
                    slug=dept_slug,
                    name=dept_name,
                )
            )

    users: dict[str, UUID] = {}
    for member in DEMO_MEMBERS:
        if member.email not in users:
            users[member.email] = new_id()
            await conn.execute(
                insert(UserRow).values(
                    id=users[member.email], email=member.email, password_hash=password_hash
                )
            )
        membership_id = new_id()
        tenant_id = tenants[member.tenant]
        await conn.execute(
            insert(MembershipRow).values(
                id=membership_id,
                user_id=users[member.email],
                tenant_id=tenant_id,
                role=member.role,
            )
        )
        for dept in member.departments:
            await conn.execute(
                insert(MembershipDepartmentRow).values(
                    membership_id=membership_id,
                    department_id=departments[(member.tenant, dept)],
                    tenant_id=tenant_id,
                )
            )
    print(f"seeded {len(tenants)} tenants, {len(departments)} departments, {len(users)} users")


async def seed_demo() -> None:
    url = os.environ.get("KB_DATABASE__MIGRATION_URL")
    password = os.environ.get("KB_SEED_PASSWORD")
    if not url or not password:
        raise SystemExit("KB_DATABASE__MIGRATION_URL and KB_SEED_PASSWORD must be set")
    check_password_policy(password, "")
    password_hash = await Argon2PasswordHasher().hash(password)
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await _seed(conn, password_hash)
    finally:
        await engine.dispose()


def _owner_url() -> str:
    url = os.environ.get("KB_DATABASE__MIGRATION_URL")
    if not url:
        raise SystemExit("KB_DATABASE__MIGRATION_URL must be set")
    return url


async def _purge(days: int) -> None:
    engine = create_async_engine(_owner_url())
    try:
        for p in await purge_expired_messages(engine, older_than_days=days, now=datetime.now(UTC)):
            print(f"tenant {p.tenant_id}: {p.messages} messages purged")
    finally:
        await engine.dispose()


async def _heads() -> None:
    engine = create_async_engine(_owner_url())
    try:
        for line in await audit_heads(engine, now=datetime.now(UTC)):
            print(line)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kb_assistant.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    key = sub.add_parser("generate-signing-key", help="create an RSA key for access tokens")
    key.add_argument("--out", type=Path, required=True)
    sub.add_parser("seed-demo", help="create the demo tenants, departments and users")
    purge = sub.add_parser("purge-expired-messages", help="apply the message retention period")
    purge.add_argument("--older-than-days", type=int, required=True)
    sub.add_parser("audit-heads", help="print each tenant's audit chain head as JSON lines")
    args = parser.parse_args(argv)

    if args.command == "generate-signing-key":
        generate_signing_key(args.out)
    elif args.command == "seed-demo":
        asyncio.run(seed_demo())
    elif args.command == "purge-expired-messages":
        asyncio.run(_purge(args.older_than_days))
    elif args.command == "audit-heads":
        asyncio.run(_heads())
    return 0


if __name__ == "__main__":
    sys.exit(main())
