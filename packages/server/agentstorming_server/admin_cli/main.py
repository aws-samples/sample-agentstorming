# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""`agentstorming-admin` — operator CLI, talks directly to Postgres."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from ..config import Settings
from ..domain.room import Room, RoomConfig
from ..repo.base import Database
from ..repo.invites import InviteRepo
from ..repo.rooms import RoomRepo
from ..services.sig import generate_keypair


async def _with_db(fn):
    settings = Settings()
    db = Database(settings.dsn)
    await db.connect()
    try:
        return await fn(db, settings)
    finally:
        await db.close()


async def _create_room(args) -> None:
    cfg_path = Path(args.config)
    data = yaml.safe_load(cfg_path.read_text())
    room_id = data.get("id") or cfg_path.stem
    config = RoomConfig.from_json(data.get("config", {}))
    priv, pub = generate_keypair()

    async def do(db, settings):
        rooms = RoomRepo(db)
        invites = InviteRepo(db)
        existing = await rooms.get(room_id)
        if existing is not None:
            raise SystemExit(f"room {room_id} already exists")
        room = Room(
            id=room_id, state="CREATED", config=config,
            server_pubkey=pub, server_privkey=priv,
            created_at=datetime.now(timezone.utc),
        )
        await rooms.create(room)
        # Mint moderator + owner + one participant invites
        ttl_secs = settings.invite_ttl_seconds_default
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_secs)
        _, mod_token = await invites.create(room_id, "moderator", expires)
        _, owner_token = await invites.create(room_id, "owner", expires)
        _, part_token = await invites.create(room_id, "participant", expires)
        await rooms.set_state(room_id, "ACTIVE")
        sys.stdout.write(json.dumps({
            "room_id": room_id,
            "moderator_invite": mod_token,
            "owner_invite": owner_token,
            "participant_invite": part_token,
            "expires_at": expires.isoformat(),
        }, indent=2) + "\n")

    await _with_db(do)


async def _create_invite(args) -> None:
    kind = args.kind
    room_id = args.room
    ttl_seconds = int(args.ttl) if args.ttl else None

    async def do(db, settings):
        rooms = RoomRepo(db)
        invites = InviteRepo(db)
        if await rooms.get(room_id) is None:
            raise SystemExit(f"room {room_id} does not exist")
        expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds or settings.invite_ttl_seconds_default)
        _, tok = await invites.create(room_id, kind, expires)
        sys.stdout.write(json.dumps({
            "kind": kind, "invite_token": tok, "expires_at": expires.isoformat(),
        }, indent=2) + "\n")

    await _with_db(do)


async def _stats(args) -> None:
    room_id = args.room

    async def do(db, settings):
        async with db.acquire() as conn:
            row = await conn.fetchrow("SELECT state, created_at FROM rooms WHERE id=$1", room_id)
            if not row:
                raise SystemExit(f"room {room_id} not found")
            ev = await conn.fetchrow("SELECT COUNT(*) AS c, COALESCE(MAX(seq),-1) AS max_seq FROM events WHERE room_id=$1", room_id)
            parts = await conn.fetchrow("SELECT COUNT(*) AS c FROM participants WHERE room_id=$1 AND left_at IS NULL", room_id)
            hands = await conn.fetchrow("SELECT COUNT(*) AS c FROM hands WHERE room_id=$1 AND state='PENDING'", room_id)
            grant = await conn.fetchrow("SELECT COUNT(*) AS c FROM grants WHERE room_id=$1 AND state='ACTIVE'", room_id)
        sys.stdout.write(json.dumps({
            "room_id": room_id,
            "state": row["state"],
            "created_at": row["created_at"].isoformat(),
            "events": int(ev["c"]),
            "max_seq": int(ev["max_seq"]),
            "participants": int(parts["c"]),
            "pending_hands": int(hands["c"]),
            "active_grants": int(grant["c"]),
        }, indent=2) + "\n")

    await _with_db(do)


async def _terminate(args) -> None:
    if not args.confirm:
        raise SystemExit("--confirm required")
    room_id = args.room
    async def do(db, settings):
        rooms = RoomRepo(db)
        if await rooms.get(room_id) is None:
            raise SystemExit(f"room {room_id} not found")
        await rooms.terminate(room_id, datetime.now(timezone.utc))
        sys.stdout.write(f"room {room_id} terminated\n")
    await _with_db(do)


async def _create_admin_token(args) -> None:
    from ..repo.admin_tokens import AdminTokenRepo
    async def do(db, settings):
        tok = await AdminTokenRepo(db).create(args.label)
        sys.stdout.write(json.dumps({"admin_token": tok}, indent=2) + "\n")
    await _with_db(do)


async def _register_owner_key(args) -> None:
    """Register an Ed25519 owner public key (base64url)."""
    import base64
    from ..repo.owner_keys import OwnerKeyRepo

    pad = "=" * (-len(args.pubkey) % 4)
    try:
        raw = base64.urlsafe_b64decode(args.pubkey + pad)
    except Exception as e:
        raise SystemExit(f"pubkey decode failed: {e}")
    if len(raw) != 32:
        raise SystemExit(f"pubkey must be 32 raw bytes, got {len(raw)}")

    async def do(db, settings):
        repo = OwnerKeyRepo(db)
        await repo.insert(raw, label=args.label)
        sys.stdout.write(
            json.dumps({"status": "registered", "pubkey": args.pubkey, "label": args.label}, indent=2)
            + "\n"
        )

    await _with_db(do)


async def _list_owner_keys(_args) -> None:
    import base64
    from ..repo.owner_keys import OwnerKeyRepo

    async def do(db, settings):
        rows = await OwnerKeyRepo(db).list_active()
        items = [
            {
                "pubkey": base64.urlsafe_b64encode(r["pubkey"]).rstrip(b"=").decode("ascii"),
                "label": r.get("label"),
                "registered_at": r["registered_at"].isoformat(),
            }
            for r in rows
        ]
        sys.stdout.write(json.dumps({"owner_keys": items}, indent=2) + "\n")

    await _with_db(do)


async def _revoke_owner_key(args) -> None:
    import base64
    from ..repo.owner_keys import OwnerKeyRepo

    pad = "=" * (-len(args.pubkey) % 4)
    raw = base64.urlsafe_b64decode(args.pubkey + pad)

    async def do(db, settings):
        await OwnerKeyRepo(db).revoke(raw)
        sys.stdout.write(json.dumps({"status": "revoked", "pubkey": args.pubkey}, indent=2) + "\n")

    await _with_db(do)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="agentstorming-admin")
    sub = parser.add_subparsers(dest="cmd", required=True)

    cr = sub.add_parser("create-room")
    cr.add_argument("--config", required=True)

    ci = sub.add_parser("create-invite")
    ci.add_argument("--room", required=True)
    ci.add_argument("--kind", choices=["participant", "moderator", "owner"], required=True)
    ci.add_argument("--ttl", type=int, default=None, help="seconds")

    st = sub.add_parser("stats")
    st.add_argument("--room", required=True)

    tm = sub.add_parser("terminate")
    tm.add_argument("--room", required=True)
    tm.add_argument("--confirm", action="store_true")

    at = sub.add_parser("create-admin-token")
    at.add_argument("--label", required=True)

    rok = sub.add_parser("register-owner-key", help="Register an Ed25519 owner public key")
    rok.add_argument("--pubkey", required=True, help="base64url of 32 raw Ed25519 bytes")
    rok.add_argument("--label", default="operator")

    sub.add_parser("list-owner-keys", help="List all active owner keys")

    rev = sub.add_parser("revoke-owner-key", help="Revoke an owner public key")
    rev.add_argument("--pubkey", required=True)

    args = parser.parse_args(argv)
    if args.cmd == "create-room":
        asyncio.run(_create_room(args))
    elif args.cmd == "create-invite":
        asyncio.run(_create_invite(args))
    elif args.cmd == "register-owner-key":
        asyncio.run(_register_owner_key(args))
    elif args.cmd == "list-owner-keys":
        asyncio.run(_list_owner_keys(args))
    elif args.cmd == "revoke-owner-key":
        asyncio.run(_revoke_owner_key(args))
    elif args.cmd == "stats":
        asyncio.run(_stats(args))
    elif args.cmd == "terminate":
        asyncio.run(_terminate(args))
    elif args.cmd == "create-admin-token":
        asyncio.run(_create_admin_token(args))


if __name__ == "__main__":  # pragma: no cover
    main()
