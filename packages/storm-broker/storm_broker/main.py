# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""storm-broker CLI entry point."""

from __future__ import annotations

import argparse
import asyncio
import base64
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from .audit import AuditChain
from .dlp import DLPScanner
from .policy import PolicyEngine, parse_capabilities
from .providers import (
    AwsSigV4Provider, BasicAuthProvider, BearerTokenProvider,
    OAuth2RefreshFlowProvider, RequestSignerProvider,
    AnthropicProvider, OpenAIProvider, GitHubProvider,
    SlackProvider, JiraProvider,
    StripeProvider, SendGridProvider, TwilioProvider, BitbucketProvider,
    SalesforceProvider, GoogleOAuth2Provider, MicrosoftGraphProvider,
    AtlassianOAuth2Provider,
)
from .server import BrokerServer


PROVIDER_CLASSES: dict[str, type] = {
    "bearer_token": BearerTokenProvider,
    "basic_auth": BasicAuthProvider,
    "oauth2_refresh": OAuth2RefreshFlowProvider,
    "request_signer": RequestSignerProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "github": GitHubProvider,
    "slack": SlackProvider,
    "jira": JiraProvider,
    "stripe": StripeProvider,
    "sendgrid": SendGridProvider,
    "twilio": TwilioProvider,
    "bitbucket": BitbucketProvider,
    "salesforce": SalesforceProvider,
    "google_oauth2": GoogleOAuth2Provider,
    "microsoft_graph": MicrosoftGraphProvider,
    "atlassian_oauth2": AtlassianOAuth2Provider,
    "aws_sigv4": AwsSigV4Provider,
}


def _load_config(path: Path) -> dict[str, Any]:
    if path.suffix in (".toml",):
        try:
            import tomllib  # py311+
        except ImportError:
            import tomli as tomllib  # type: ignore
        return tomllib.loads(path.read_text())
    if path.suffix in (".yaml", ".yml"):
        import yaml
        return yaml.safe_load(path.read_text())
    raise ValueError(f"unknown config format: {path.suffix}")


def _build_providers(cfg: dict[str, Any]) -> dict:
    out = {}
    for name, p in (cfg.get("providers") or {}).items():
        cls = PROVIDER_CLASSES.get(p["type"])
        if cls is None:
            raise SystemExit(f"unknown provider type: {p['type']}")
        out[name] = cls(p)
    return out


def _build_policies(cfg: dict[str, Any]) -> dict[int, PolicyEngine]:
    """Build one PolicyEngine per persona uid.

    ``room.mode`` seeds every engine. A per-persona ``mode`` may only be
    *stricter* than the room's: capability sets narrow, never widen
    (Stage-13 §Capability declaration), and the same principle applies to
    the mode gate.
    """
    room_mode = ((cfg.get("room") or {}).get("mode") or "active")
    if room_mode not in ("planning", "active"):
        raise SystemExit(f"invalid room.mode: {room_mode!r} (planning|active)")
    out: dict[int, PolicyEngine] = {}
    for entry in cfg.get("personas") or []:
        uid = int(entry["uid"])
        caps = parse_capabilities(entry.get("capabilities", []))
        mode = entry.get("mode", room_mode)
        if mode not in ("planning", "active"):
            raise SystemExit(f"invalid mode for uid {uid}: {mode!r}")
        if room_mode == "planning":
            mode = "planning"
        out[uid] = PolicyEngine(caps, room_mode=mode)
    return out


def _room_pubkey(cfg: dict[str, Any]) -> bytes | None:
    """Decode ``room.server_pubkey`` (base64url, 32 raw bytes) if present."""
    raw = (cfg.get("room") or {}).get("server_pubkey")
    if not raw:
        return None
    pad = "=" * (-len(raw) % 4)
    key = base64.urlsafe_b64decode(raw + pad)
    if len(key) != 32:
        raise SystemExit("room.server_pubkey must decode to 32 bytes")
    return key


async def _run(args: argparse.Namespace) -> int:
    cfg = _load_config(Path(args.config))
    providers = _build_providers(cfg)
    policies = _build_policies(cfg)
    allowed_uids = set(policies.keys())
    audit_path = Path(cfg.get("audit_log", "/var/lib/agentstorming/audit.ndjson"))
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    hmac_key_path = Path(cfg.get("audit_hmac_key", str(audit_path.parent / ".audit-hmac-key")))
    if not hmac_key_path.exists():
        hmac_key_path.write_bytes(secrets.token_bytes(32))
        os.chmod(hmac_key_path, 0o600)
    chain = AuditChain(audit_path, hmac_key_path.read_bytes())
    server = BrokerServer(
        socket_path=cfg.get("socket_path", "/run/agentstorming/broker.sock"),
        providers=providers,
        policy_for_uid=policies,
        audit_chain=chain,
        allowed_uids=allowed_uids,
        dlp=DLPScanner(),
        room_id=(cfg.get("room") or {}).get("room_id"),
        server_pubkey=_room_pubkey(cfg),
    )
    await server.serve_forever()
    return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="storm-broker")
    sub = p.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="Run the broker daemon")
    run.add_argument("--config", required=True, help="path to broker config (toml/yaml)")
    run.add_argument("--log-level", default="INFO")
    audit = sub.add_parser("audit", help="Audit-log helpers")
    audit.add_argument("action", choices=["verify", "tail"])
    audit.add_argument("--path", default="/var/lib/agentstorming/audit.ndjson")
    audit.add_argument("--key", default="/var/lib/agentstorming/.audit-hmac-key")
    args = p.parse_args()
    if args.cmd == "run":
        logging.basicConfig(level=args.log_level,
                            format="%(asctime)s %(levelname)s %(name)s %(message)s")
        sys.exit(asyncio.run(_run(args)))
    if args.cmd == "audit":
        path = Path(args.path)
        key = Path(args.key).read_bytes()
        chain = AuditChain(path, key)
        if args.action == "verify":
            ok, n = chain.verify()
            print(f"verified: {ok}, entries: {n}")
            sys.exit(0 if ok else 1)
        if args.action == "tail":
            if path.exists():
                print(path.read_text())


if __name__ == "__main__":
    main()
