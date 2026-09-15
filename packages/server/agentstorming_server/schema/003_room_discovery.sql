-- Public-room discovery metadata + interview + runs_as flag.
-- Adds human-readable title/description on rooms, a runs_as column on
-- participants (§7.2), and a table for registration interview sessions
-- (§8.3-§8.4).

ALTER TABLE rooms
    ADD COLUMN IF NOT EXISTS title       TEXT,
    ADD COLUMN IF NOT EXISTS description TEXT;

CREATE INDEX IF NOT EXISTS rooms_visibility_idx
    ON rooms ((config->>'visibility'))
    WHERE state IN ('CREATED', 'ACTIVE');

ALTER TABLE participants
    ADD COLUMN IF NOT EXISTS runs_as TEXT
        CHECK (runs_as IN ('agent', 'human'))
        DEFAULT 'agent';

-- Record of public-registration interview sessions. Any candidate who
-- called /register gets a row here; state machine drives the whisper
-- channel and the accept/reject transitions.
CREATE TABLE IF NOT EXISTS interviews (
    id            TEXT PRIMARY KEY,
    room_id       TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    candidate_pid TEXT NOT NULL,
    moderator_pid TEXT,
    state         TEXT NOT NULL
                    CHECK (state IN ('PENDING', 'ACTIVE', 'ACCEPTED', 'REJECTED', 'ABANDONED')),
    declared      TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS interviews_room_state_idx ON interviews(room_id, state);

-- Public-rooms door state (§8.4). When the moderator closes the door for
-- a duration, new /register calls are rejected.
CREATE TABLE IF NOT EXISTS registration_doors (
    room_id    TEXT PRIMARY KEY REFERENCES rooms(id) ON DELETE CASCADE,
    closed_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    reopens_at TIMESTAMPTZ NOT NULL,
    reason     TEXT
);
