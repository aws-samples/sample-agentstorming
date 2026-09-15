-- §9.3/§13.3 — per-room dedicated owner pubkey, used by
-- /v1/owner/{room_id}/regenerate_token (signed challenge) and by the
-- owner to prove they hold the key associated with this specific
-- room. This is distinct from the server-wide owner_keys seeded at
-- boot; a room may register multiple keys (rotation).
CREATE TABLE IF NOT EXISTS room_owner_keys (
    room_id        TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    pubkey         BYTEA NOT NULL,
    label          TEXT,
    registered_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at     TIMESTAMPTZ,
    PRIMARY KEY (room_id, pubkey)
);

CREATE INDEX IF NOT EXISTS room_owner_keys_active_idx
    ON room_owner_keys(room_id)
    WHERE revoked_at IS NULL;
