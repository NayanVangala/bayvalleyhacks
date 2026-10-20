-- OnRecord database schema for SQLite.
-- This file mirrors schema.sql (the Postgres 16 dialect) table for table.
-- Types are kept portable (TEXT, INTEGER, REAL) and timestamps are stored
-- as TEXT ISO 8601 strings so application SQL is identical across dialects.

CREATE TABLE IF NOT EXISTS people (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'recording',
    source TEXT NOT NULL DEFAULT 'glasses',
    rehearsal INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    speaker_person_id TEXT REFERENCES people(id),
    speaker_label TEXT NOT NULL DEFAULT 'Unknown',
    attribution_source TEXT NOT NULL DEFAULT 'UNKNOWN',
    face_score REAL,
    voice_score REAL,
    text TEXT NOT NULL DEFAULT '',
    raw_text TEXT,
    avg_logprob REAL,
    no_speech_prob REAL,
    gate_status TEXT NOT NULL DEFAULT 'REVIEW',
    uncertain INTEGER NOT NULL DEFAULT 0,
    start_offset_s REAL NOT NULL DEFAULT 0,
    end_offset_s REAL NOT NULL DEFAULT 0,
    audio_path TEXT,
    created_at TEXT NOT NULL
);

-- corrections is append-only: rows are never updated or deleted.
-- The speaker columns are nullable (an unassign records NULL as the new
-- speaker) but when set they must name a real person, so a typo can
-- never write an audit row pointing at nobody.
CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    segment_id TEXT NOT NULL REFERENCES segments(id),
    old_speaker_person_id TEXT REFERENCES people(id),
    new_speaker_person_id TEXT REFERENCES people(id),
    operator_id TEXT NOT NULL DEFAULT 'wearer',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slack_outbox (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    posted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_segments_session_seq ON segments (session_id, seq);
CREATE INDEX IF NOT EXISTS idx_segments_speaker_person ON segments (speaker_person_id);
CREATE INDEX IF NOT EXISTS idx_segments_gate_status ON segments (gate_status);
CREATE INDEX IF NOT EXISTS idx_corrections_segment ON corrections (segment_id);
CREATE INDEX IF NOT EXISTS idx_sessions_started_at ON sessions (started_at);
