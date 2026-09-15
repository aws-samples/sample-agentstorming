# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""Runtime configuration for the Storm server.

Loaded from environment variables with sensible defaults. Production
deployments should set every value explicitly via the deployment
environment or a secrets manager.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level settings."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTSTORMING_",
        env_file=None,
        extra="ignore",
    )

    # --- persistence -----------------------------------------------------
    # No password in the default. A literal credential here is one every
    # reader of this repository already has, and defaults have a way of
    # reaching production. Local development relies on Postgres peer/trust
    # auth for the current OS user; everything else sets AGENTSTORMING_DSN.
    dsn: str = Field(
        "postgresql://127.0.0.1:5432/agentstorming",
        description=(
            "Postgres DSN. Supply credentials via this variable — e.g. "
            "postgresql://<user>:<password>@<host>:5432/<dbname> — or rely on "
            "peer authentication. The default carries no password."
        ),
    )

    # --- http ------------------------------------------------------------
    # Binds all interfaces on purpose: the server is designed to sit behind
    # nginx (single-VM) or an ALB (cloud), which terminate TLS and are the
    # only things that should reach it. Narrow this with the deployment's
    # security group / listen address, not by guessing an interface here.
    bind_host: str = "0.0.0.0"  # nosec B104 - always fronted by nginx or an ALB
    bind_port: int = 8440

    # --- CORS -----------------------------------------------------------
    # CSV list of permitted origins. Default "*" stays permissive for
    # development convenience; production deployments MUST set this to
    # the actual SPA origin (e.g. "https://app.example.com"). When
    # explicit origins are configured, allow_credentials becomes true so
    # browsers will send cookies on cross-origin XHR; with "*" it stays
    # off (browsers refuse the combination per CORS spec).
    cors_allow_origins: str = "*"
    cors_allow_methods: str = "*"
    cors_allow_headers: str = "*"

    # --- public origin --------------------------------------------------
    # The origin the server is reached at from outside, e.g.
    # "https://storm.example.com". Used to build invite and attachment links.
    #
    # Set it in any deployment that sits behind a proxy. When it is empty the
    # links fall back to Starlette's request.base_url, which is reconstructed
    # from the request's Host header — so it is whatever the client sent, or
    # whatever the last proxy rewrote it to. Two consequences, one cosmetic and
    # one not:
    #
    #   * Behind CloudFront -> ALB -> ECS the Host reaching the app is the
    #     origin's, not the public domain, so invite links point somewhere the
    #     invitee cannot reach.
    #   * An invite link carries a live single-use invite token in its query
    #     string. A Host the operator does not control therefore ends up inside
    #     a URL holding a credential. Issuing the link needs moderator rights
    #     already, so this is not an escalation — but a link that points where
    #     the request asked rather than where the deployment lives is the wrong
    #     default for something carrying a token.
    public_base_url: str = ""

    # --- security headers -----------------------------------------------
    # When set, the server emits Strict-Transport-Security on every
    # response. Format is the raw header value; recommended production
    # default: "max-age=63072000; includeSubDomains; preload".
    hsts_header: str | None = None
    # X-Frame-Options. "DENY" by default; set to "" to disable.
    x_frame_options: str = "DENY"
    # X-Content-Type-Options. "nosniff" by default; set to "" to disable.
    x_content_type_options: str = "nosniff"
    # Referrer-Policy.
    referrer_policy: str = "no-referrer"

    # --- admin -----------------------------------------------------------
    # Both of the following are declared and read by nothing. They are kept so
    # that AGENTSTORMING_ADMIN_TOKEN / _ADMIN_BIND_HOST in an existing
    # deployment do not become `extra="ignore"` surprises, but neither has any
    # effect and the descriptions say so rather than describing behaviour that
    # was never implemented.
    #
    # Admin bearer tokens are validated against the `admin_tokens` table, which
    # stores SHA-256 hashes and permits several valid tokens at once. Issue one
    # with `agentstorming-admin create-admin-token`. That is the only path.
    admin_token: str | None = Field(
        default=None,
        description=(
            "DEPRECATED and ignored. Setting this does NOT authenticate "
            "anything: admin requests are checked against the admin_tokens "
            "table, not against this value. Nothing generates or prints a "
            "token at startup. Use `agentstorming-admin create-admin-token`."
        ),
    )
    # The admin routes are mounted on the same application, and therefore the
    # same listener, as everything else. This setting does not move them and
    # never did; treating the admin surface as loopback-only because of it
    # would be a mistake.
    admin_bind_host: str = Field(
        default="127.0.0.1",
        description=(
            "DEPRECATED and ignored. The admin routes share the main bind "
            "host and port. Restrict them at the ALB, security group, or "
            "reverse proxy, not here."
        ),
    )

    # --- attachments -----------------------------------------------------
    attachments_backend: Literal["local", "s3"] = "local"
    attachments_local_dir: Path = Path("/var/lib/agentstorming/attachments")
    attachments_s3_bucket: str | None = None
    attachments_s3_region: str | None = None
    attachments_max_bytes: int = 52_428_800  # 50 MiB

    # --- tokens ----------------------------------------------------------
    access_token_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 86_400
    invite_ttl_seconds_default: int = 7 * 86_400

    # --- replay ----------------------------------------------------------
    clock_skew_seconds: int = 30
    nonce_window: int = 256

    # --- rate limits -----------------------------------------------------
    # These are the defaults the spec mandates in §20.8. They were 60/10/120
    # here — six to ten times looser than the document that describes them —
    # so a reader comparing the two found the reference server weaker than its
    # own specification. Published sample code has to be secure by default;
    # a deployer who needs more can raise any of these via AGENTSTORMING_*.
    #
    # 1 raise-hand per minute looks tight until you note what it protects:
    # the moderator's attention is the scarce resource in a moderated room,
    # not server CPU. Posting is separately gated by turn-taking when
    # raise_hand_required is set, so the post limit is a second layer.
    rate_limit_post_per_minute: int = 10
    rate_limit_raise_hand_per_minute: int = 1
    rate_limit_sync_per_minute: int = 60

    # --- logging ---------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True

    # --- governance -----------------------------------------------------
    governance_tick_seconds: float = 1.0

    # --- metrics ---------------------------------------------------------
    enable_metrics: bool = True

    # --- registration filter --------------------------------------------
    registration_filter: str | None = Field(
        default=None,
        description=(
            "Optional Python import path (``pkg.module:callable``) that "
            "receives the decoded RegisterBody + client metadata and may "
            "deny a candidate. Callable signature: "
            "``(body: dict, meta: dict) -> None | {'deny': str}``. "
            "Return None / empty to allow; return a dict with ``deny`` "
            "to reject with that reason. Runs after pubkey + rate checks."
        ),
    )

    # --- owner key bootstrap --------------------------------------------
    owner_pubkey: str | None = Field(
        default=None,
        description=(
            "base64url Ed25519 public key of the human owner / deployer. "
            "Seeded into owner_keys on first boot if the table is empty."
        ),
    )
    owner_pubkey_label: str = "bootstrap"
    owner_nonce_ttl_seconds: int = 60


def load_settings() -> Settings:
    return Settings()
