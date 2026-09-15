# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""``agentstorming`` CLI — the user-facing entry point.

Subcommands:

- ``owner init-key``        Generate a fresh Ed25519 owner keypair.
- ``owner print-pubkey``    Print the base64url public key.
- ``owner request-invite``  Sign a request to the server and print a
                            fresh invite token.
- ``install-skill``         Copy the Agent Storming skill into a tool's
                            discovery path (claude-code, codex, kiro,
                            windsurf, cursor, deepagents, ...).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import installer, owner as owner_mod


def _cmd_owner_init_key(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser() if args.path else owner_mod.DEFAULT_KEY_PATH
    try:
        _priv, pub = owner_mod.init_key(path, overwrite=args.force)
    except FileExistsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    from .signing import b64url
    print(json.dumps({
        "path": str(path),
        "public_key_b64": b64url(pub),
    }, indent=2))
    return 0


def _cmd_owner_print_pubkey(args: argparse.Namespace) -> int:
    path = Path(args.path).expanduser() if args.path else None
    try:
        print(owner_mod.print_pubkey(path))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    return 0


def _check_insecure(insecure: bool, base_url: str) -> bool:
    """Gate --insecure to local hosts, and say so loudly when it is used.

    Disabling certificate verification on an owner-invite request means an
    interceptor can pose as the server for a call that carries signing
    material. That is defensible against a self-signed cert on your own
    machine and indefensible anywhere else, so the remote case is refused
    rather than warned about — a warning in a script's output is a warning
    nobody reads.
    """
    if not insecure:
        return False
    from urllib.parse import urlparse
    host = (urlparse(base_url).hostname or "").lower()
    local = host in ("localhost", "127.0.0.1", "::1", "") or host.endswith(".localhost")
    if not local:
        raise SystemExit(
            f"refusing --insecure against {host!r}: TLS verification may only "
            "be skipped for a local development server. Install the server's "
            "certificate, or use a real one."
        )
    print(
        "WARNING: TLS verification disabled. Acceptable only for a "
        "self-signed local development server.",
        file=sys.stderr,
    )
    return True


def _cmd_owner_request_invite(args: argparse.Namespace) -> int:
    async def run() -> int:
        try:
            resp = await owner_mod.request_invite(
                base_url=args.base_url,
                room_id=args.room,
                kind=args.kind,
                key_path=Path(args.path).expanduser() if args.path else None,
                verify_tls=not _check_insecure(args.insecure, args.base_url),
            )
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(json.dumps(resp, indent=2))
        return 0

    return asyncio.run(run())


def _cmd_owner_approve(args: argparse.Namespace) -> int:
    """Owner-side: respond to an outstanding broker approval_request.

    Talks directly to a local storm-broker over its Unix socket. The
    broker's record_approval RPC accepts {granted, denied, revoked}.
    """
    from .broker_client import BrokerClient, BrokerError
    c = BrokerClient(socket_path=args.broker_socket)
    try:
        res = c._call("record_approval", {
            "approval_id": args.approval_id,
            "decision": args.decision,
        })
    except BrokerError as e:
        print(f"ERROR: {e.message} ({e.data})", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 0


def _cmd_install_skill(args: argparse.Namespace) -> int:
    try:
        dst = installer.install(args.target, force=args.force)
    except (ValueError, FileExistsError, FileNotFoundError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    print(json.dumps({"target": args.target, "installed_to": str(dst)}, indent=2))
    return 0


def _cmd_list_targets(_args: argparse.Namespace) -> int:
    for t in installer.list_targets():
        print(t)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentstorming")
    sub = p.add_subparsers(dest="cmd", required=True)

    owner = sub.add_parser("owner", help="Owner-key operations")
    o_sub = owner.add_subparsers(dest="owner_cmd", required=True)

    oi = o_sub.add_parser("init-key", help="Generate owner keypair")
    oi.add_argument("--path", default=None)
    oi.add_argument("--force", action="store_true")
    oi.set_defaults(func=_cmd_owner_init_key)

    op = o_sub.add_parser("print-pubkey", help="Print public key (base64url)")
    op.add_argument("--path", default=None)
    op.set_defaults(func=_cmd_owner_print_pubkey)

    orq = o_sub.add_parser("request-invite", help="Sign + post an owner-key invite request")
    orq.add_argument("--base-url", required=True)
    orq.add_argument("--room", required=True)
    orq.add_argument("--kind", default="owner", choices=["participant", "moderator", "owner"])
    orq.add_argument("--path", default=None)
    orq.add_argument(
        "--insecure", action="store_true",
        help=(
            "DANGEROUS: skip TLS certificate verification. Only for a "
            "self-signed localhost server during development. This request "
            "carries owner signing material, so over any non-local network an "
            "attacker who can intercept it can impersonate the server."
        ),
    )
    orq.set_defaults(func=_cmd_owner_request_invite)

    oa = o_sub.add_parser("approve", help="Approve/deny a pending broker approval_request")
    oa.add_argument("--approval-id", required=True)
    oa.add_argument("--decision", default="granted",
                    choices=["granted", "denied", "revoked"])
    oa.add_argument("--broker-socket", default="/run/agentstorming/broker.sock")
    oa.set_defaults(func=_cmd_owner_approve)

    inst = sub.add_parser("install-skill", help="Copy the Agent Storming skill into a tool")
    inst.add_argument("--target", required=True, help="e.g. claude-code, codex, kiro, windsurf, cursor, deepagents")
    inst.add_argument("--force", action="store_true")
    inst.set_defaults(func=_cmd_install_skill)

    inst_list = sub.add_parser("list-skill-targets", help="List supported install targets")
    inst_list.set_defaults(func=_cmd_list_targets)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    return func(args)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
