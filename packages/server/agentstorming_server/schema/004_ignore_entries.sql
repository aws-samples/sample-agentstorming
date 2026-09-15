-- Per-moderator view-local ignore list (§12.1a). See spec for semantics:
-- the row represents "moderator_pid does not want to see whisper traffic
-- (and optionally registration_requests) from ignored_pid".
CREATE TABLE IF NOT EXISTS ignore_entries (
    room_id      TEXT NOT NULL REFERENCES rooms(id) ON DELETE CASCADE,
    moderator_pid TEXT NOT NULL,
    ignored_pid  TEXT NOT NULL,
    kind         TEXT NOT NULL CHECK (kind IN ('muted', 'candidate')),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (room_id, moderator_pid, ignored_pid)
);

CREATE INDEX IF NOT EXISTS ignore_entries_viewer_idx
    ON ignore_entries(room_id, moderator_pid);
