-- §4.7 key_revocation: once a participant self-revokes their key, all
-- subsequent events (and token use) under that pubkey must be rejected.
-- We track the revocation timestamp on the participant row itself.
ALTER TABLE participants
    ADD COLUMN IF NOT EXISTS revoked_at TIMESTAMPTZ;
