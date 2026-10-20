import type {
  Person,
  QueryResult,
  Segment,
  Session,
  Stats,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// ---------------------------------------------------------------------------
// Server wire format vs dashboard format
//
// The Python server does not send the field names the dashboard renders:
//   segments: speaker_label, start_offset_s, end_offset_s
//   sessions: source (not capture_source), lifecycle routes live under
//             /sessions (not /api/sessions), action calls return a
//             { session, state, new_segments } wrapper, and an ended
//             session is stored with status "ready"
//   people:   created_at (not enrolled_at)
//   segments for a session come from GET /api/sessions/{id}, which returns
//             { ...session, segments: [...] }; there is no
//             /api/sessions/{id}/segments endpoint and no /api/stats.
// Every response is normalized here, at the boundary, so components only
// ever see the dashboard shapes declared in ./types.
// ---------------------------------------------------------------------------

type Raw = Record<string, unknown>;

function asRecord(value: unknown): Raw | null {
  if (value !== null && typeof value === "object" && !Array.isArray(value)) {
    return value as Raw;
  }
  return null;
}

function str(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function num(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : fallback;
}

function optNum(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

async function errorFromResponse(
  res: Response,
  label: string,
): Promise<Error> {
  let detail = "";
  try {
    const data: unknown = await res.json();
    const record = asRecord(data);
    if (record && typeof record.detail === "string") {
      detail = record.detail;
    } else if (typeof data === "string") {
      detail = data;
    }
  } catch {
    detail = "";
  }
  const suffix = detail ? `: ${detail}` : "";
  return new Error(`${label} failed: ${res.status} ${res.statusText}${suffix}`);
}

export function normalizePerson(raw: unknown): Person {
  const record = asRecord(raw) ?? {};
  const createdAt = str(record.created_at) || undefined;
  return {
    id: str(record.id),
    name: str(record.name, "Unknown"),
    enrolled_at: str(record.enrolled_at) || createdAt,
    created_at: createdAt,
    notes: typeof record.notes === "string" ? record.notes : undefined,
    segment_count: optNum(record.segment_count),
    talk_time_s: optNum(record.talk_time_s),
  };
}

function normalizeStatus(record: Raw): string {
  let status = str(record.status).toLowerCase();
  const state = str(record.state).toLowerCase();
  if (!status && state) {
    status = state;
  }
  // The server stores a cleanly ended session as "ready" in the DB, and
  // mirrors controller states (idle, armed, recording, paused, ended)
  // into the same field while a session is live.
  if (status === "ready" || status === "complete" || status === "completed") {
    return "ended";
  }
  return status || "unknown";
}

export function normalizeSession(raw: unknown): Session {
  const outer = asRecord(raw) ?? {};
  // Action endpoints wrap the session: { session, state, new_segments }.
  const inner = asRecord(outer.session);
  const record: Raw = inner ? { ...outer, ...inner } : outer;
  if (inner && !record.state && outer.state !== undefined) {
    record.state = outer.state;
  }
  const sourceRaw = record.source ?? record.capture_source;
  const source =
    sourceRaw === "phone" || sourceRaw === "glasses" ? sourceRaw : undefined;
  const session: Session = {
    id: str(record.id) || str(record.session_id),
    title: typeof record.title === "string" ? record.title : undefined,
    status: normalizeStatus(record),
    started_at:
      typeof record.started_at === "string" ? record.started_at : undefined,
    ended_at:
      typeof record.ended_at === "string" || record.ended_at === null
        ? (record.ended_at as string | null)
        : undefined,
    capture_source: source,
    source: typeof sourceRaw === "string" ? sourceRaw : undefined,
    segment_count: optNum(record.segment_count),
  };
  if (Array.isArray(outer.new_segments)) {
    session.new_segments = outer.new_segments.map((item) =>
      normalizeSegment(item, session.started_at),
    );
  }
  return session;
}

export function normalizeSegment(
  raw: unknown,
  sessionStartedAt?: string,
): Segment {
  const record = asRecord(raw) ?? {};
  const startS = num(record.start_s ?? record.start_offset_s);
  const endS = num(record.end_s ?? record.end_offset_s, startS);
  const speakerName =
    str(record.speaker_name) || str(record.speaker_label) || "Unknown";
  let startIso =
    typeof record.start_iso === "string" ? record.start_iso : undefined;
  if (!startIso && sessionStartedAt) {
    const base = new Date(sessionStartedAt);
    if (!Number.isNaN(base.getTime())) {
      startIso = new Date(base.getTime() + startS * 1000).toISOString();
    }
  }
  const gate = str(record.gate_status);
  return {
    id: str(record.id) || str(record.segment_id),
    session_id: str(record.session_id),
    seq: num(record.seq),
    speaker_person_id:
      typeof record.speaker_person_id === "string"
        ? record.speaker_person_id
        : null,
    speaker_name: speakerName,
    speaker_label: str(record.speaker_label) || undefined,
    text: str(record.text),
    gate_status:
      gate === "VERIFIED" || gate === "REVIEW" || gate === "SILENCE"
        ? gate
        : "REVIEW",
    attribution_source:
      typeof record.attribution_source === "string"
        ? record.attribution_source
        : undefined,
    start_s: startS,
    end_s: endS,
    start_iso: startIso,
  };
}

export function sortSegments(segments: Segment[]): Segment[] {
  return [...segments].sort(
    (a, b) => a.seq - b.seq || a.start_s - b.start_s,
  );
}

// ---------------------------------------------------------------------------
// Health and stats
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<{ ok: boolean; data: unknown }> {
  try {
    const res = await fetch(`${API_BASE}/healthz`, { cache: "no-store" });
    let data: unknown = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }
    return { ok: res.ok, data };
  } catch {
    return { ok: false, data: null };
  }
}

export async function getStats(): Promise<Stats> {
  // There is no /api/stats endpoint on the server. Aggregate the counts
  // from the endpoints that do exist. Collection counts (Chroma) are not
  // exposed over HTTP at all, so they stay undefined and the status page
  // renders them as unknown instead of inventing a number.
  const [people, sessions] = await Promise.all([listPeople(), listSessions()]);
  let segments: number | undefined = 0;
  await Promise.all(
    sessions.map(async (session) => {
      try {
        const detail = await getSession(session.id);
        if (detail?.segment_count !== undefined) {
          segments =
            segments === undefined
              ? undefined
              : segments + detail.segment_count;
        } else {
          const rows = await getSegments(session.id);
          segments =
            segments === undefined ? undefined : segments + rows.length;
        }
      } catch {
        segments = undefined;
      }
    }),
  );
  const stats: Stats = {
    people: people.length,
    sessions: sessions.length,
  };
  if (segments !== undefined) {
    stats.segments = segments;
  }
  return stats;
}

// ---------------------------------------------------------------------------
// People
// ---------------------------------------------------------------------------

export async function listPeople(): Promise<Person[]> {
  const res = await fetch(`${API_BASE}/api/people`, { cache: "no-store" });
  if (!res.ok) {
    throw await errorFromResponse(res, "listPeople");
  }
  const data: unknown = await res.json();
  if (Array.isArray(data)) return data.map(normalizePerson);
  const record = asRecord(data);
  if (record && Array.isArray(record.people)) {
    return record.people.map(normalizePerson);
  }
  return [];
}

function blobToBase64(blob: Blob): Promise<string> {
  return blob.arrayBuffer().then((buffer) => {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    const CHUNK = 0x8000;
    for (let offset = 0; offset < bytes.length; offset += CHUNK) {
      binary += String.fromCharCode(
        ...bytes.subarray(offset, offset + CHUNK),
      );
    }
    return btoa(binary);
  });
}

export async function enrollPerson(fd: FormData): Promise<Person> {
  // The server endpoint (POST /api/people/enroll, alias of /api/enroll)
  // reads a JSON body { name, photo_b64, voice_b64, notes? }. It never
  // parses multipart form data, so sending the FormData through as-is
  // always failed with a 422. Convert the files to base64 here.
  const name = fd.get("name");
  const photo = fd.get("photo");
  const voice = fd.get("voice");
  if (typeof name !== "string" || !name.trim()) {
    throw new Error("enrollPerson failed: a name is required");
  }
  if (!(photo instanceof Blob) || !(voice instanceof Blob)) {
    throw new Error(
      "enrollPerson failed: a photo and a voice sample are required",
    );
  }
  const [photoB64, voiceB64] = await Promise.all([
    blobToBase64(photo),
    blobToBase64(voice),
  ]);
  const res = await fetch(`${API_BASE}/api/people/enroll`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: name.trim(),
      photo_b64: photoB64,
      voice_b64: voiceB64,
    }),
    cache: "no-store",
  });
  if (!res.ok) {
    throw await errorFromResponse(res, "enrollPerson");
  }
  return normalizePerson(await res.json());
}

// ---------------------------------------------------------------------------
// Sessions
// ---------------------------------------------------------------------------

export async function listSessions(): Promise<Session[]> {
  const res = await fetch(`${API_BASE}/api/sessions`, {
    cache: "no-store",
  });
  if (!res.ok) {
    throw await errorFromResponse(res, "listSessions");
  }
  const data: unknown = await res.json();
  if (Array.isArray(data)) return data.map(normalizeSession);
  const record = asRecord(data);
  if (record && Array.isArray(record.sessions)) {
    return record.sessions.map(normalizeSession);
  }
  return [];
}

export async function createSession(title?: string): Promise<Session> {
  // Session creation lives at POST /sessions, not /api/sessions (a POST
  // there is a 405 on the server).
  const res = await fetch(`${API_BASE}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(title ? { title } : {}),
    cache: "no-store",
  });
  if (!res.ok) {
    throw await errorFromResponse(res, "createSession");
  }
  return normalizeSession(await res.json());
}

export async function sessionAction(
  id: string,
  action: "arm" | "start" | "pause" | "resume" | "end",
): Promise<Session> {
  // Lifecycle routes are POST /sessions/{id}/{action}, and the response
  // wraps the session as { session, state, new_segments }.
  // normalizeSession unwraps it and keeps any segments the action sealed
  // (pause and end seal the open segment) on session.new_segments.
  const res = await fetch(
    `${API_BASE}/sessions/${encodeURIComponent(id)}/${action}`,
    {
      method: "POST",
      cache: "no-store",
    },
  );
  if (!res.ok) {
    throw await errorFromResponse(res, `sessionAction(${action})`);
  }
  return normalizeSession(await res.json());
}

export async function getSession(id: string): Promise<Session | null> {
  const res = await fetch(
    `${API_BASE}/api/sessions/${encodeURIComponent(id)}`,
    { cache: "no-store" },
  );
  if (res.status === 404) return null;
  if (!res.ok) {
    throw await errorFromResponse(res, "getSession");
  }
  const data: unknown = await res.json();
  const record = asRecord(data);
  const session = normalizeSession(data);
  if (record && Array.isArray(record.segments)) {
    session.segment_count = record.segments.length;
  }
  return session;
}

export async function getSegments(sessionId: string): Promise<Segment[]> {
  // There is no /api/sessions/{id}/segments endpoint. The session detail
  // endpoint returns the session plus its segments in one payload.
  const res = await fetch(
    `${API_BASE}/api/sessions/${encodeURIComponent(sessionId)}`,
    { cache: "no-store" },
  );
  if (!res.ok) {
    throw await errorFromResponse(res, "getSegments");
  }
  const data: unknown = await res.json();
  const record = asRecord(data);
  if (!record) return [];
  const session = normalizeSession(data);
  const list = Array.isArray(record.segments) ? record.segments : [];
  return sortSegments(
    list.map((item) => normalizeSegment(item, session.started_at)),
  );
}

// ---------------------------------------------------------------------------
// Search
// ---------------------------------------------------------------------------

export async function searchMemory(
  q: string,
  personId?: string,
): Promise<QueryResult> {
  let res: Response;
  try {
    const params = new URLSearchParams({ q });
    if (personId) params.set("person_id", personId);
    res = await fetch(`${API_BASE}/api/search?${params.toString()}`, {
      cache: "no-store",
    });
  } catch {
    return { status: "EMPTY", reason: "BACKEND_UNREACHABLE", results: [] };
  }
  if (!res.ok) {
    // Surface the server's own reason (FastAPI detail) when it sent one,
    // instead of reporting every failure as an unreachable backend.
    let reason = `SERVER_ERROR_${res.status}`;
    try {
      const data: unknown = await res.json();
      const record = asRecord(data);
      if (record && typeof record.detail === "string" && record.detail) {
        reason = record.detail;
      }
    } catch {
      // Keep the status based reason.
    }
    return { status: "EMPTY", reason, results: [] };
  }
  const data: unknown = await res.json();
  if (Array.isArray(data)) {
    const results = data as QueryResult["results"];
    return {
      status: results.length > 0 ? "OK" : "EMPTY",
      reason: results.length > 0 ? null : "NO_MATCHES",
      results,
    };
  }
  const record = asRecord(data);
  if (record) {
    const results = Array.isArray(record.results)
      ? (record.results as QueryResult["results"])
      : [];
    const status: QueryResult["status"] =
      record.status === "OK" || record.status === "EMPTY"
        ? record.status
        : results.length > 0
          ? "OK"
          : "EMPTY";
    const reason = typeof record.reason === "string" ? record.reason : null;
    return { status, reason, results };
  }
  return { status: "EMPTY", reason: "NO_MATCHES", results: [] };
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

export function formatClock(totalSeconds: number): string {
  const safe = Number.isFinite(totalSeconds)
    ? Math.max(0, Math.floor(totalSeconds))
    : 0;
  const minutes = Math.floor(safe / 60);
  const seconds = safe % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export function formatTime(iso?: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString();
}
