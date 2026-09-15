-- AgentStorm v1 database schema.
-- Applied once at server startup; idempotent via IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS rooms (
    id             TEXT PRIMARY KEY,
    state          TEXT NOT NULL CHECK (state IN ('CREATED','ACTIVE','FROZEN','TERMINATED')),
    config         JSONB NOT NULL,
    server_pubkey  BYTEA NOT NULL,
    server_privkey BYTEA NOT NULL,
    freeze_since   TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminated_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS participants (
    room_id        TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    pid            TEXT NOT NULL,
    pubkey         BYTEA NOT NULL,
    affiliation    TEXT NOT NULL,
    deputy_rank    INT,
    joined_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    left_at        TIMESTAMPTZ,
    penned_until   TIMESTAMPTZ,
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, pid)
);

CREATE UNIQUE INDEX IF NOT EXISTS participants_room_pubkey_idx
    ON participants(room_id, pubkey);

CREATE UNIQUE INDEX IF NOT EXISTS participants_room_rank_idx
    ON participants(room_id, deputy_rank)
    WHERE deputy_rank IS NOT NULL;

CREATE INDEX IF NOT EXISTS participants_affiliation_idx
    ON participants(room_id, affiliation);

CREATE TABLE IF NOT EXISTS events (
    room_id      TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    seq          BIGINT NOT NULL,
    id           UUID NOT NULL,
    type         TEXT NOT NULL,
    sender       TEXT NOT NULL,
    ts_sender    TIMESTAMPTZ NOT NULL,
    ts_server    TIMESTAMPTZ NOT NULL DEFAULT now(),
    iat          TIMESTAMPTZ NOT NULL,
    nonce        TEXT NOT NULL,
    reply_to     BIGINT,
    mentions     TEXT[] NOT NULL DEFAULT '{}',
    payload      JSONB NOT NULL,
    sig          JSONB NOT NULL,
    raw          JSONB NOT NULL,
    PRIMARY KEY (room_id, seq)
);

CREATE INDEX IF NOT EXISTS events_type_idx ON events(room_id, type, seq);
CREATE INDEX IF NOT EXISTS events_sender_idx ON events(room_id, sender, seq);

CREATE TABLE IF NOT EXISTS invites (
    id             UUID PRIMARY KEY,
    room_id        TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    kind           TEXT NOT NULL CHECK (kind IN ('participant','moderator','owner')),
    token_hash     BYTEA NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    consumed_at    TIMESTAMPTZ,
    consumed_by    TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS invites_token_hash_idx ON invites(token_hash);

CREATE TABLE IF NOT EXISTS tokens (
    id             UUID PRIMARY KEY,
    room_id        TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    pid            TEXT NOT NULL,
    kind           TEXT NOT NULL CHECK (kind IN ('access','refresh')),
    token_hash     BYTEA NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    revoked_at     TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS tokens_token_hash_idx ON tokens(token_hash);
CREATE INDEX IF NOT EXISTS tokens_pid_idx ON tokens(room_id, pid, kind);
CREATE INDEX IF NOT EXISTS tokens_expires_idx ON tokens(expires_at);

CREATE TABLE IF NOT EXISTS nonces (
    pid            TEXT NOT NULL,
    nonce          TEXT NOT NULL,
    seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (pid, nonce)
);

CREATE INDEX IF NOT EXISTS nonces_seen_at_idx ON nonces(pid, seen_at);

CREATE TABLE IF NOT EXISTS hands (
    room_id      TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    hand_id      UUID NOT NULL,
    pid          TEXT NOT NULL,
    state        TEXT NOT NULL CHECK (state IN ('PENDING','GRANTED','WITHDRAWN','DISMISSED','CONSUMED','EXPIRED')),
    raised_ts    TIMESTAMPTZ NOT NULL,
    closed_ts    TIMESTAMPTZ,
    hint         TEXT,
    PRIMARY KEY (room_id, hand_id)
);

CREATE INDEX IF NOT EXISTS hands_active_idx ON hands(room_id, pid)
    WHERE state IN ('PENDING','GRANTED');

CREATE TABLE IF NOT EXISTS grants (
    room_id        TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    grant_id       UUID NOT NULL,
    pid            TEXT NOT NULL,
    hand_id        UUID,
    granted_ts     TIMESTAMPTZ NOT NULL,
    ttl_expires_at TIMESTAMPTZ NOT NULL,
    state          TEXT NOT NULL CHECK (state IN ('ACTIVE','CONSUMED','EXPIRED')),
    consumed_ts    TIMESTAMPTZ,
    PRIMARY KEY (room_id, grant_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS grants_one_active_per_room
    ON grants(room_id) WHERE state = 'ACTIVE';

CREATE TABLE IF NOT EXISTS documents (
    room_id         TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    id              UUID NOT NULL,
    type            TEXT NOT NULL,
    title           TEXT NOT NULL,
    body            TEXT,
    attachment_ref  TEXT,
    content_type    TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    created_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, id)
);

CREATE TABLE IF NOT EXISTS summaries (
    room_id         TEXT PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
    text            TEXT NOT NULL,
    updated_by      TEXT NOT NULL,
    updated_ts      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mutes (
    room_id         TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    pid             TEXT NOT NULL,
    started_at      TIMESTAMPTZ NOT NULL,
    expires_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (room_id, pid, started_at)
);

CREATE INDEX IF NOT EXISTS mutes_active_idx ON mutes(room_id, pid, expires_at);

CREATE TABLE IF NOT EXISTS idempotency (
    key            TEXT NOT NULL,
    pid            TEXT NOT NULL,
    status         INT NOT NULL,
    body           JSONB NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (key, pid)
);

CREATE INDEX IF NOT EXISTS idempotency_created_at_idx ON idempotency(created_at);

CREATE TABLE IF NOT EXISTS attachments (
    id              UUID PRIMARY KEY,
    room_id         TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    sender_pid      TEXT NOT NULL,
    content_type    TEXT NOT NULL,
    size_bytes      BIGINT NOT NULL,
    filename        TEXT NOT NULL,
    sha256          BYTEA NOT NULL,
    s3_key          TEXT,
    local_path      TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS attachments_room_idx ON attachments(room_id, created_at);

CREATE TABLE IF NOT EXISTS registration_requests (
    id              UUID PRIMARY KEY,
    room_id         TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    pubkey          BYTEA NOT NULL,
    declared        TEXT NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('PENDING','ACCEPTED','REJECTED')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS admin_tokens (
    token_hash     BYTEA PRIMARY KEY,
    label          TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS owner_challenges (
    room_id        TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    nonce          TEXT NOT NULL,
    expires_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (room_id, nonce)
);

-- periodic cleanup: handled by a governance task.
