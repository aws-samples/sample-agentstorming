# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: MIT-0

"""FastAPI app factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import Settings
from .repo.admin_tokens import AdminTokenRepo
from .repo.attachments import AttachmentRepo
from .repo.base import Database
from .repo.documents import DocumentRepo, SummaryRepo
from .repo.events import EventRepo
from .repo.grants import GrantRepo
from .repo.hands import HandRepo
from .repo.idempotency import IdempotencyRepo
from .repo.invites import InviteRepo
from .repo.mutes import MuteRepo
from .repo.nonces import NonceRepo
from .repo.owner_keys import OwnerKeyRepo, OwnerNonceRepo
from .repo.participants import ParticipantRepo
from .repo.rooms import RoomRepo
from .repo.tokens import TokenRepo
from .routers import (
    v1_attachments,
    v1_capabilities,
    v1_claim,
    v1_discovery,
    v1_documents,
    v1_events,
    v1_history,
    v1_moderation,
    v1_owner,
    v1_registration,
    v1_stream,
    v1_summary,
)
from .services import sig as sig_mod
from .services.attachments import LocalAttachmentStorage, S3AttachmentStorage
from .services.auth import AuthGate
from .services.events_app import PostEventService
from .services.governance import GovernanceTicker
from .services.pubsub import PubSubHub
from .services.rate_limit import RateLimiter
from .services.replay import ReplayGuard
from .services.snapshot import SnapshotService
from .services.system_events import SystemEventFactory

log = logging.getLogger("agentstorming.server")


def load_sql(base_dir: Path) -> str:
    parts = []
    for p in sorted(base_dir.glob("*.sql")):
        parts.append(p.read_text())
    return "\n\n".join(parts)


def _resolve_registration_filter(path: str | None):
    """Resolve ``pkg.module:callable`` to a callable, or return None.

    Any import failure is logged and the filter is left disabled — an
    operator misconfiguration must not prevent boot.
    """
    if not path:
        return None
    if ":" not in path:
        log.error("AGENTSTORMING_REGISTRATION_FILTER must be 'pkg.module:callable' (got %r)", path)
        return None
    modname, _, fnname = path.partition(":")
    try:
        import importlib
        mod = importlib.import_module(modname)
        fn = getattr(mod, fnname)
        if not callable(fn):
            log.error("registration filter %s is not callable", path)
            return None
        log.info("registration filter loaded: %s", path)
        return fn
    except Exception as e:  # pragma: no cover - operator config error
        log.error("failed to load registration filter %s: %s", path, e)
        return None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    settings: Settings = app.state.settings
    db = Database(settings.dsn)
    await db.connect()

    # Apply schema (idempotent). Empty string would upset asyncpg, so
    # guard explicitly in case package-data didn't ship the .sql files.
    schema_dir = Path(__file__).parent / "schema"
    sql = load_sql(schema_dir)
    if not sql.strip():
        log.warning("no schema files found under %s — schema NOT applied", schema_dir)
    else:
        async with db.acquire() as conn:
            await conn.execute(sql)

    # Repos
    app.state.db = db
    app.state.rooms = RoomRepo(db)
    app.state.participants = ParticipantRepo(db)
    app.state.events = EventRepo(db)
    app.state.invites = InviteRepo(db)
    app.state.tokens = TokenRepo(db)
    app.state.hands = HandRepo(db)
    app.state.grants = GrantRepo(db)
    app.state.mutes = MuteRepo(db)
    app.state.documents = DocumentRepo(db)
    app.state.summaries = SummaryRepo(db)
    app.state.attachments = AttachmentRepo(db)
    app.state.idempotency = IdempotencyRepo(db)
    app.state.nonces = NonceRepo(db)
    app.state.admin_tokens = AdminTokenRepo(db)
    app.state.owner_keys = OwnerKeyRepo(db)
    app.state.owner_nonces = OwnerNonceRepo(db, ttl_seconds=settings.owner_nonce_ttl_seconds)
    from .repo.interviews import InterviewRepo, RegistrationDoorRepo
    app.state.interviews = InterviewRepo(db)
    app.state.registration_doors = RegistrationDoorRepo(db)
    from .repo.ignore_entries import IgnoreEntryRepo
    app.state.ignore_entries = IgnoreEntryRepo(db)
    from .repo.room_owner_keys import RoomOwnerKeyRepo
    app.state.room_owner_keys = RoomOwnerKeyRepo(db)

    # Owner key seeding (first boot only).
    if settings.owner_pubkey and not await app.state.owner_keys.has_any():
        import base64
        pad = "=" * (-len(settings.owner_pubkey) % 4)
        try:
            raw_pub = base64.urlsafe_b64decode(settings.owner_pubkey + pad)
        except Exception as e:
            log.error("AGENTSTORMING_OWNER_PUBKEY decode failed: %s", e)
        else:
            if len(raw_pub) == 32:
                await app.state.owner_keys.insert(raw_pub, label=settings.owner_pubkey_label)
                log.info("seeded owner key (label=%s)", settings.owner_pubkey_label)
            else:
                log.error(
                    "AGENTSTORMING_OWNER_PUBKEY is %d bytes; expected 32 (Ed25519 raw)",
                    len(raw_pub),
                )

    # Services
    app.state.auth = AuthGate(app.state.tokens, app.state.participants, app.state.admin_tokens)
    app.state.replay = ReplayGuard(app.state.nonces, settings.clock_skew_seconds, settings.nonce_window)
    app.state.rate_limiter = RateLimiter(
        post_per_minute=settings.rate_limit_post_per_minute,
        raise_hand_per_minute=settings.rate_limit_raise_hand_per_minute,
        sync_per_minute=settings.rate_limit_sync_per_minute,
    )
    app.state.registration_filter = _resolve_registration_filter(settings.registration_filter)
    app.state.snapshot = SnapshotService(
        app.state.rooms, app.state.participants, app.state.hands, app.state.grants,
        app.state.documents, app.state.summaries, app.state.events,
    )

    app.state.sys_factory = SystemEventFactory(app.state.rooms)

    async def sys_publish_event(room_id: str, etype: str, *, payload: dict, mentions=None):
        """Build a system-posted event, sign it, store it in the log with
        signature-preserving bytes, and return the committed Envelope.
        """
        env, raw = await app.state.sys_factory.build(room_id, etype, payload=payload, mentions=mentions)
        result = await app.state.events.append(room_id, env, raw)
        try:
            app.state.telemetry.events_published.add(
                1, attributes={"room_id": room_id, "type": etype, "sys": "true"}
            )
            if etype == "org.agentstorming.mute":
                app.state.telemetry.mutes.add(1, attributes={"room_id": room_id})
            elif etype == "org.agentstorming.participant_left" and (payload or {}).get("reason") == "ejected":
                app.state.telemetry.ejects.add(1, attributes={"room_id": room_id})
            elif etype == "org.agentstorming.affiliation_changed" and (payload or {}).get("new_affiliation") == "penned":
                app.state.telemetry.pens.add(1, attributes={"room_id": room_id})
            elif etype == "org.agentstorming.registration_request":
                app.state.telemetry.interviews_started.add(1, attributes={"room_id": room_id})
            elif etype == "org.agentstorming.registration_accepted":
                app.state.telemetry.interviews_accepted.add(1, attributes={"room_id": room_id})
            elif etype == "org.agentstorming.registration_rejected":
                app.state.telemetry.interviews_rejected.add(1, attributes={"room_id": room_id})
        except Exception:
            pass
        return result

    app.state.sys_publish_event = sys_publish_event
    # Backward-compatible alias used by older call sites that still build then manually append.
    # It returns only the Envelope; callers that need the raw_dict should use sys_factory.build directly.
    async def sys_build_event(room_id: str, etype: str, *, payload: dict, mentions=None):
        env, _raw = await app.state.sys_factory.build(room_id, etype, payload=payload, mentions=mentions)
        return env
    app.state.sys_build_event = sys_build_event

    def sys_sign(room_id: str, env_dict: dict) -> str:
        # Synchronous sign used by snapshot.build_envelope. We must load room privkey synchronously.
        # Cheap cache in app.state.
        room = app.state._sync_room_cache.get(room_id)
        if not room:
            # Lazy: spawn a one-off blocking fetch isn't possible here; we require the caller to have
            # warmed the cache. Snapshot builder is always called after a room is known to exist, and
            # we warm the cache whenever a room is loaded via SnapshotService.build_envelope.
            raise RuntimeError("room privkey not cached for sync signing")
        return sig_mod.sign_envelope(env_dict, room.server_privkey)

    app.state._sync_room_cache = {}

    # Override snapshot.build_envelope to warm the cache.
    original_build_envelope = app.state.snapshot.build_envelope
    async def build_envelope(room_id: str, *, sign_func):
        room = await app.state.rooms.get(room_id)
        if room is None:
            raise ValueError(f"unknown room {room_id}")
        app.state._sync_room_cache[room_id] = room
        return await original_build_envelope(room_id, sign_func=sign_func)
    app.state.snapshot.build_envelope = build_envelope  # type: ignore[assignment]
    app.state.sys_sign = sys_sign

    app.state.post_event = PostEventService(
        app.state.rooms, app.state.participants, app.state.events, app.state.hands,
        app.state.grants, app.state.mutes, app.state.replay, sys_build_event,
        tokens=app.state.tokens,
    )

    # Pub/sub hub (backs SSE stream)
    app.state.pubsub = PubSubHub(settings.dsn)
    await app.state.pubsub.start()

    # Governance — passes the publish helper so governance-emitted
    # system events preserve signature bytes across storage.
    app.state.governance = GovernanceTicker(
        db, app.state.rooms, app.state.participants, app.state.hands, app.state.grants,
        app.state.mutes, app.state.events, app.state.tokens,
        tick_seconds=settings.governance_tick_seconds,
        build_system_event=sys_build_event,
        publish_system_event=sys_publish_event,
        snapshot=app.state.snapshot,
    )
    await app.state.governance.start()

    # Attachment store
    if settings.attachments_backend == "s3":
        if not settings.attachments_s3_bucket or not settings.attachments_s3_region:
            raise RuntimeError("s3 backend requires attachments_s3_bucket + attachments_s3_region")
        app.state.attachments_store = S3AttachmentStorage(
            settings.attachments_s3_bucket, settings.attachments_s3_region,
        )
    else:
        app.state.attachments_store = LocalAttachmentStorage(
            settings.attachments_local_dir,
            public_url_base=f"http://{settings.bind_host}:{settings.bind_port}",
        )

    try:
        yield
    finally:
        await app.state.governance.stop()
        await app.state.pubsub.stop()
        await db.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Agent Storming Server", version="0.1.0", lifespan=_lifespan)
    app.state.settings = settings
    from .services.telemetry import telemetry
    telemetry.init(app)
    app.state.telemetry = telemetry

    def _csv(s: str) -> list[str]:
        return [x.strip() for x in s.split(",") if x.strip()]

    # CORS. Default "*" stays permissive for development; production
    # MUST set AGENTSTORMING_CORS_ALLOW_ORIGINS to a concrete origin
    # list. When concrete origins are configured, we also opt into
    # allow_credentials so cookies / Authorization can be sent.
    cors_origins = _csv(settings.cors_allow_origins)
    using_wildcard = cors_origins == ["*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=_csv(settings.cors_allow_methods),
        allow_headers=_csv(settings.cors_allow_headers),
        allow_credentials=not using_wildcard,
    )

    # Static security headers — applied to every response. These are
    # cheap defence-in-depth that only get in the way of a malicious
    # caller. HSTS is opt-in (operator must set the header explicitly
    # because not every deployment is HTTPS).
    @app.middleware("http")
    async def _security_headers(request, call_next):
        response = await call_next(request)
        if settings.hsts_header:
            response.headers.setdefault("Strict-Transport-Security",
                                        settings.hsts_header)
        if settings.x_frame_options:
            response.headers.setdefault("X-Frame-Options",
                                        settings.x_frame_options)
        if settings.x_content_type_options:
            response.headers.setdefault("X-Content-Type-Options",
                                        settings.x_content_type_options)
        if settings.referrer_policy:
            response.headers.setdefault("Referrer-Policy",
                                        settings.referrer_policy)
        return response

    app.include_router(v1_stream.router, prefix="/v1/rooms")
    app.include_router(v1_events.router, prefix="/v1/rooms")
    app.include_router(v1_claim.router, prefix="/v1/rooms")
    app.include_router(v1_claim.router, prefix="/v1")  # for tokens/refresh (used bare)
    app.include_router(v1_moderation.router, prefix="/v1/rooms")
    app.include_router(v1_history.router, prefix="/v1/rooms")
    app.include_router(v1_summary.router, prefix="/v1/rooms")
    app.include_router(v1_registration.router, prefix="/v1/rooms")
    app.include_router(v1_documents.router, prefix="/v1/rooms")
    app.include_router(v1_capabilities.router, prefix="/v1")
    app.include_router(v1_attachments.router, prefix="/v1")
    app.include_router(v1_owner.router, prefix="/v1")
    app.include_router(v1_discovery.router, prefix="/v1")

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/readyz")
    async def readyz():
        try:
            async with app.state.db.acquire() as conn:
                await conn.execute("SELECT 1")
            return {"ok": True}
        except Exception as e:  # pragma: no cover
            return {"ok": False, "error": str(e)}

    return app
