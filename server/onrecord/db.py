"""Relational store for OnRecord (people, sessions, segments, corrections).

This module is the only place that talks to the relational database.
It runs on the standard library ``sqlite3`` driver by default and only
imports ``psycopg`` lazily when ``settings.database_url`` points at
Postgres. That keeps local development and tests free of any native
database dependency while letting production use Postgres with the
exact same SQL and the exact same schema (see ``schema.sql`` and
``schema_sqlite.sql`` next to this file).

Conventions:
- Primary keys are ``uuid.uuid4().hex`` strings minted here.
- Timestamps are ISO 8601 strings from
  ``datetime.now(timezone.utc).isoformat()``.
- Boolean fields (``rehearsal``, ``uncertain``) are stored as 0/1
  integers so both dialects agree, and are converted back to real
  booleans in the dicts returned to callers.
- Every public method returns plain ``dict`` objects (or lists of
  dicts), never driver row objects.
- SQL is written once with ``?`` placeholders. For Postgres the
  placeholders are translated to ``%s`` at execution time.
- Thread safety: a single shared connection created with
  ``check_same_thread=False`` (sqlite) plus a ``threading.RLock``
  around every operation, so the store is safe to share across
  FastAPI worker threads.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

__all__ = ["Store"]

_BOOL_FIELDS = ("rehearsal", "uncertain")
_PACKAGE_DIR = Path(__file__).parent


def _now_iso() -> str:
    """Current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Fresh primary key."""
    return uuid.uuid4().hex


def _bool_to_int(value) -> int:
    return 1 if value else 0


def _setting(settings, name: str, default=None):
    """Read ``name`` from a Settings object or a plain dict."""
    if settings is None:
        return default
    if isinstance(settings, dict):
        value = settings.get(name, default)
    else:
        value = getattr(settings, name, default)
    return default if value is None else value


class Store:
    """Thread safe relational store backed by sqlite3 or Postgres.

    Parameters
    ----------
    settings:
        An ``onrecord.config.Settings`` instance. When
        ``settings.database_url`` starts with ``"postgres"`` the
        store connects through psycopg (imported lazily at connect
        time). Otherwise it opens a sqlite database at
        ``settings.db_path``.
    """

    def __init__(self, settings):
        self.settings = settings
        database_url = _setting(settings, "database_url", "") or ""
        if not isinstance(database_url, str):
            database_url = str(database_url)
        self._database_url = database_url
        self._is_postgres = database_url.startswith("postgres")

        db_path = _setting(settings, "db_path", None)
        if db_path is None:
            db_path = _setting(settings, "sqlite_path", "onrecord.db")
        self._db_path = db_path

        self._conn = None
        self._lock = threading.RLock()
        self._schema_ready = False

    # ------------------------------------------------------------------
    # Connection and schema
    # ------------------------------------------------------------------

    def _connect(self):
        """Create the underlying connection if it does not exist yet.

        Caller must hold ``self._lock``. Schema initialisation runs
        automatically right after the first successful connect.
        """
        if self._conn is not None:
            return self._conn
        if self._is_postgres:
            # Lazy import: psycopg is only needed for Postgres URLs,
            # so sqlite only installs never pay for it.
            import psycopg
            from psycopg.rows import dict_row

            self._conn = psycopg.connect(
                self._database_url, row_factory=dict_row
            )
        else:
            path = Path(self._db_path)
            if str(path) != ":memory:":
                parent = path.parent
                if str(parent) not in ("", "."):
                    parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys = ON")
            except Exception:
                pass
            self._conn = conn
        self.init_schema()
        return self._conn

    def _ensure_conn(self):
        with self._lock:
            return self._connect()

    def init_schema(self) -> None:
        """Create tables and indexes from the bundled schema file.

        Postgres uses ``schema.sql`` and sqlite uses
        ``schema_sqlite.sql``; if the preferred file is missing the
        other one is tried, since the two files are kept identical
        apart from dialect notes. Safe to call more than once: the
        schema uses ``IF NOT EXISTS`` throughout.
        """
        with self._lock:
            if self._conn is None:
                # Inline the connect step so init_schema can be the
                # public entry point without recursing into itself.
                if self._is_postgres:
                    import psycopg
                    from psycopg.rows import dict_row

                    self._conn = psycopg.connect(
                        self._database_url, row_factory=dict_row
                    )
                else:
                    path = Path(self._db_path)
                    if str(path) != ":memory:":
                        parent = path.parent
                        if str(parent) not in ("", "."):
                            parent.mkdir(parents=True, exist_ok=True)
                    conn = sqlite3.connect(
                        str(path), check_same_thread=False
                    )
                    conn.row_factory = sqlite3.Row
                    try:
                        conn.execute("PRAGMA foreign_keys = ON")
                    except Exception:
                        pass
                    self._conn = conn

            preferred = (
                _PACKAGE_DIR / "schema.sql"
                if self._is_postgres
                else _PACKAGE_DIR / "schema_sqlite.sql"
            )
            fallback = (
                _PACKAGE_DIR / "schema_sqlite.sql"
                if self._is_postgres
                else _PACKAGE_DIR / "schema.sql"
            )
            schema_path = preferred if preferred.exists() else fallback
            if not schema_path.exists():
                raise FileNotFoundError(
                    "No schema file found next to db.py: "
                    f"tried {preferred} and {fallback}"
                )
            sql = schema_path.read_text(encoding="utf-8")

            cursor = self._conn.cursor()
            try:
                if self._is_postgres:
                    # psycopg has no executescript; run one
                    # statement at a time. Comment only chunks are
                    # valid no-ops for Postgres, so they are kept.
                    for statement in sql.split(";"):
                        stmt = statement.strip()
                        if stmt:
                            cursor.execute(stmt)
                else:
                    cursor.executescript(sql)
                self._conn.commit()
            finally:
                cursor.close()
            self._schema_ready = True

    def close(self) -> None:
        """Close the underlying connection, if one is open."""
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None
                    self._schema_ready = False

    def __enter__(self) -> "Store":
        self._ensure_conn()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Low level helpers (caller holds the lock via public wrappers)
    # ------------------------------------------------------------------

    def _translate(self, sql: str) -> str:
        """Translate ``?`` placeholders to ``%s`` for Postgres."""
        if self._is_postgres:
            return sql.replace("?", "%s")
        return sql

    @staticmethod
    def _to_dict(row) -> Optional[dict]:
        if row is None:
            return None
        result = dict(row)
        for field_name in _BOOL_FIELDS:
            if field_name in result and isinstance(result[field_name], int):
                result[field_name] = bool(result[field_name])
        return result

    def _fetchone(self, sql: str, params=()) -> Optional[dict]:
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(self._translate(sql), tuple(params))
            row = cursor.fetchone()
        finally:
            cursor.close()
        return self._to_dict(row)

    def _fetchall(self, sql: str, params=()) -> list[dict]:
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(self._translate(sql), tuple(params))
            rows = cursor.fetchall()
        finally:
            cursor.close()
        return [self._to_dict(row) for row in rows]

    def _execute_write(self, sql: str, params=()) -> None:
        conn = self._connect()
        cursor = conn.cursor()
        try:
            cursor.execute(self._translate(sql), tuple(params))
            conn.commit()
        finally:
            cursor.close()

    # ------------------------------------------------------------------
    # People
    # ------------------------------------------------------------------

    def create_person(self, name, notes="") -> dict:
        with self._lock:
            person_id = _new_id()
            now = _now_iso()
            self._execute_write(
                "INSERT INTO people (id, name, notes, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (person_id, name, notes or "", now, now),
            )
            return self.get_person(person_id)

    def get_person(self, person_id) -> Optional[dict]:
        with self._lock:
            return self._fetchone(
                "SELECT * FROM people WHERE id = ?", (person_id,)
            )

    def list_people(self) -> list[dict]:
        with self._lock:
            return self._fetchall(
                "SELECT * FROM people ORDER BY created_at ASC"
            )

    def find_people_by_name(self, name) -> list[dict]:
        """Case insensitive exact name match."""
        with self._lock:
            return self._fetchall(
                "SELECT * FROM people WHERE lower(name) = lower(?)"
                " ORDER BY created_at ASC",
                (name,),
            )

    def update_person(self, person_id, name=None, notes=None) -> Optional[dict]:
        with self._lock:
            existing = self.get_person(person_id)
            if existing is None:
                return None
            assignments = []
            params: list = []
            if name is not None:
                assignments.append("name = ?")
                params.append(name)
            if notes is not None:
                assignments.append("notes = ?")
                params.append(notes)
            assignments.append("updated_at = ?")
            params.append(_now_iso())
            params.append(person_id)
            self._execute_write(
                f"UPDATE people SET {', '.join(assignments)} WHERE id = ?",
                tuple(params),
            )
            return self.get_person(person_id)

    def people_count(self) -> int:
        with self._lock:
            row = self._fetchone("SELECT COUNT(*) AS n FROM people")
            return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def create_session(self, title="", source="glasses", rehearsal=False) -> dict:
        with self._lock:
            session_id = _new_id()
            now = _now_iso()
            self._execute_write(
                "INSERT INTO sessions"
                " (id, title, status, source, rehearsal, started_at, ended_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id,
                    title or "",
                    "recording",
                    source or "glasses",
                    _bool_to_int(rehearsal),
                    now,
                    None,
                ),
            )
            return self.get_session(session_id)

    def get_session(self, session_id) -> Optional[dict]:
        with self._lock:
            return self._fetchone(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            )

    def list_sessions(self) -> list[dict]:
        """Sessions, newest first."""
        with self._lock:
            return self._fetchall(
                "SELECT * FROM sessions ORDER BY started_at DESC"
            )

    def set_session_status(self, session_id, status) -> Optional[dict]:
        with self._lock:
            self._execute_write(
                "UPDATE sessions SET status = ? WHERE id = ?",
                (status, session_id),
            )
            return self.get_session(session_id)

    def end_session(self, session_id) -> Optional[dict]:
        """Mark a session ended: status ``ready`` plus ``ended_at``."""
        with self._lock:
            self._execute_write(
                "UPDATE sessions SET status = ?, ended_at = ? WHERE id = ?",
                ("ready", _now_iso(), session_id),
            )
            return self.get_session(session_id)

    # ------------------------------------------------------------------
    # Segments
    # ------------------------------------------------------------------

    def add_segment(
        self,
        session_id,
        seq,
        text,
        raw_text=None,
        avg_logprob=None,
        no_speech_prob=None,
        gate_status="REVIEW",
        uncertain=False,
        start_offset_s=0,
        end_offset_s=0,
        audio_path=None,
    ) -> dict:
        """Insert a transcribed segment with no speaker attribution yet.

        The speaker starts NULL with label ``Unknown`` and
        attribution source ``UNKNOWN``; identity is attached later
        through :meth:`set_segment_speaker`.
        """
        with self._lock:
            segment_id = _new_id()
            self._execute_write(
                "INSERT INTO segments"
                " (id, session_id, seq, speaker_person_id, speaker_label,"
                " attribution_source, face_score, voice_score, text,"
                " raw_text, avg_logprob, no_speech_prob, gate_status,"
                " uncertain, start_offset_s, end_offset_s, audio_path,"
                " created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    segment_id,
                    session_id,
                    seq,
                    None,
                    "Unknown",
                    "UNKNOWN",
                    None,
                    None,
                    text,
                    raw_text,
                    avg_logprob,
                    no_speech_prob,
                    gate_status,
                    _bool_to_int(uncertain),
                    start_offset_s,
                    end_offset_s,
                    audio_path,
                    _now_iso(),
                ),
            )
            return self.get_segment(segment_id)

    def set_segment_speaker(
        self,
        segment_id,
        person_id,
        label,
        source,
        face_score=None,
        voice_score=None,
    ) -> Optional[dict]:
        with self._lock:
            self._execute_write(
                "UPDATE segments SET speaker_person_id = ?,"
                " speaker_label = ?, attribution_source = ?,"
                " face_score = ?, voice_score = ? WHERE id = ?",
                (person_id, label, source, face_score, voice_score, segment_id),
            )
            return self.get_segment(segment_id)

    def get_segment(self, segment_id) -> Optional[dict]:
        with self._lock:
            return self._fetchone(
                "SELECT * FROM segments WHERE id = ?", (segment_id,)
            )

    def list_segments(self, session_id) -> list[dict]:
        """Segments of one session, ordered by sequence number."""
        with self._lock:
            return self._fetchall(
                "SELECT * FROM segments WHERE session_id = ?"
                " ORDER BY seq ASC",
                (session_id,),
            )

    def segments_for_person(self, person_id, gate_status=None) -> list[dict]:
        with self._lock:
            if gate_status is None:
                return self._fetchall(
                    "SELECT * FROM segments WHERE speaker_person_id = ?"
                    " ORDER BY created_at ASC",
                    (person_id,),
                )
            return self._fetchall(
                "SELECT * FROM segments WHERE speaker_person_id = ?"
                " AND gate_status = ? ORDER BY created_at ASC",
                (person_id, gate_status),
            )

    def search_segments_exact(self, term, limit=50) -> list[dict]:
        """Case insensitive substring search over segment text.

        The term is matched literally: ``%``, ``_`` and the escape
        character itself are escaped so they cannot act as LIKE
        wildcards. Results are newest first.
        """
        with self._lock:
            escaped = (
                str(term)
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
            pattern = f"%{escaped}%"
            return self._fetchall(
                "SELECT * FROM segments"
                " WHERE lower(text) LIKE lower(?) ESCAPE '\\'"
                " ORDER BY created_at DESC LIMIT ?",
                (pattern, int(limit)),
            )

    # ------------------------------------------------------------------
    # Corrections
    # ------------------------------------------------------------------

    def add_correction(
        self, segment_id, old_person_id, new_person_id, operator_id="wearer"
    ) -> dict:
        with self._lock:
            correction_id = _new_id()
            self._execute_write(
                "INSERT INTO corrections"
                " (id, segment_id, old_speaker_person_id,"
                " new_speaker_person_id, operator_id, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    correction_id,
                    segment_id,
                    old_person_id,
                    new_person_id,
                    operator_id or "wearer",
                    _now_iso(),
                ),
            )
            return self._fetchone(
                "SELECT * FROM corrections WHERE id = ?", (correction_id,)
            )

    def list_corrections(self, segment_id=None) -> list[dict]:
        with self._lock:
            if segment_id is None:
                return self._fetchall(
                    "SELECT * FROM corrections ORDER BY created_at ASC"
                )
            return self._fetchall(
                "SELECT * FROM corrections WHERE segment_id = ?"
                " ORDER BY created_at ASC",
                (segment_id,),
            )

    # ------------------------------------------------------------------
    # Slack outbox
    # ------------------------------------------------------------------

    def enqueue_slack(self, session_id, body) -> dict:
        with self._lock:
            outbox_id = _new_id()
            self._execute_write(
                "INSERT INTO slack_outbox"
                " (id, session_id, body, status, created_at, posted_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (outbox_id, session_id, body, "queued", _now_iso(), None),
            )
            return self._fetchone(
                "SELECT * FROM slack_outbox WHERE id = ?", (outbox_id,)
            )

    def list_outbox(self, status=None) -> list[dict]:
        with self._lock:
            if status is None:
                return self._fetchall(
                    "SELECT * FROM slack_outbox ORDER BY created_at ASC"
                )
            return self._fetchall(
                "SELECT * FROM slack_outbox WHERE status = ?"
                " ORDER BY created_at ASC",
                (status,),
            )

    def mark_outbox_posted(self, outbox_id) -> Optional[dict]:
        with self._lock:
            self._execute_write(
                "UPDATE slack_outbox SET status = ?, posted_at = ?"
                " WHERE id = ?",
                ("posted", _now_iso(), outbox_id),
            )
            return self._fetchone(
                "SELECT * FROM slack_outbox WHERE id = ?", (outbox_id,)
            )
