-- Owner public keys (server-wide, human owner / deployer).
--
-- Each row is an Ed25519 public key registered as authoritative for the
-- deployer of this server. Keys are seeded on first boot from the
-- AGENTSTORM_OWNER_PUBKEY env var; additional keys can be added via a
-- signed request from an existing owner key (POST /v1/owner/keys).

CREATE TABLE IF NOT EXISTS owner_keys (
    pubkey        BYTEA PRIMARY KEY,
    label         TEXT,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS owner_keys_active_idx
    ON owner_keys (pubkey)
    WHERE revoked_at IS NULL;

-- One-shot nonces for signed requests (prevents replay).
-- Issued via GET /v1/owner/nonce (signed) and the registration nonce route;
-- consumed by a signed owner request, reads included, or a candidate's
-- credential collection; expired rows are purged by the governance ticker.
CREATE TABLE IF NOT EXISTS owner_nonces (
    nonce        TEXT PRIMARY KEY,
    issued_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at   TIMESTAMPTZ NOT NULL,
    consumed_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS owner_nonces_expiry_idx ON owner_nonces (expires_at);
