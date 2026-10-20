"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import LiveTranscript from "../components/LiveTranscript";
import SessionControls from "../components/SessionControls";
import SpeakerCards from "../components/SpeakerCards";
import {
  createSession,
  getSegments,
  listPeople,
  listSessions,
  normalizeSegment,
  sortSegments,
  sessionAction,
} from "../lib/api";
import type { LiveEvent, Person, Segment, Session } from "../lib/types";
import { LiveClient } from "../lib/ws";

type CaptureSource = "glasses" | "phone";

const ACTIVE_STATUSES = new Set(["recording", "live", "paused", "armed"]);
const POLL_INTERVAL_MS = 3000;

type EventShape = {
  type?: string;
  segment?: unknown;
  segments?: unknown;
  session?: Session;
  person_id?: string | null;
  speaker_person_id?: string | null;
  speaker_id?: string | null;
  active_speaker_id?: string | null;
  capture_source?: string;
  captureSource?: string;
  source?: string;
  session_id?: string;
};

function captureSourceFromRecord(
  record: Record<string, unknown> | null | undefined,
): CaptureSource | null {
  if (!record) {
    return null;
  }
  const value = record.capture_source ?? record.captureSource ?? record.source;
  if (value === "phone" || value === "glasses") {
    return value;
  }
  return null;
}

function captureSourceFromSession(session: Session | null): CaptureSource | null {
  if (!session) {
    return null;
  }
  return captureSourceFromRecord(session as unknown as Record<string, unknown>);
}

function messageFromError(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback;
}

export default function LivePage() {
  const [people, setPeople] = useState<Person[]>([]);
  const [session, setSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [activeSpeakerId, setActiveSpeakerId] = useState<string | null>(null);
  const [captureSource, setCaptureSource] = useState<CaptureSource>("glasses");
  const [connectionState, setConnectionState] = useState<string>("disconnected");
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const sessionRef = useRef<Session | null>(null);
  const knownSegmentIdsRef = useRef<Set<string>>(new Set());

  const applySession = useCallback((next: Session | null) => {
    sessionRef.current = next;
    setSession(next);
    const source = captureSourceFromSession(next);
    if (source) {
      setCaptureSource(source);
    }
  }, []);

  // Upsert segments (a correction can resend an existing segment id with
  // a new speaker, so an existing id is replaced, not dropped), and track
  // the newest speaker as the active one.
  const mergeSegments = useCallback((incoming: Segment[]) => {
    if (incoming.length === 0) {
      return;
    }
    const currentId = sessionRef.current?.id ?? null;
    const relevant = incoming.filter(
      (segment) =>
        !segment.session_id ||
        !currentId ||
        segment.session_id === currentId,
    );
    if (relevant.length === 0) {
      return;
    }
    let newestNew: Segment | null = null;
    for (const segment of relevant) {
      if (segment.id && !knownSegmentIdsRef.current.has(segment.id)) {
        newestNew = segment;
      }
      if (segment.id) {
        knownSegmentIdsRef.current.add(segment.id);
      }
    }
    setSegments((previous) => {
      const byId = new Map(previous.map((segment) => [segment.id, segment]));
      for (const segment of relevant) {
        byId.set(segment.id, segment);
      }
      return sortSegments([...byId.values()]);
    });
    if (newestNew?.speaker_person_id) {
      setActiveSpeakerId(newestNew.speaker_person_id);
    }
  }, []);

  const replaceSegments = useCallback((next: Segment[]) => {
    knownSegmentIdsRef.current = new Set(
      next.map((segment) => segment.id).filter(Boolean),
    );
    setSegments(sortSegments(next));
  }, []);

  const switchToSession = useCallback(
    (next: Session) => {
      const previousId = sessionRef.current?.id ?? null;
      applySession(next);
      if (next.id !== previousId) {
        // Session switch: nothing from the old session may leak into
        // the new transcript.
        replaceSegments([]);
        setActiveSpeakerId(null);
        void getSegments(next.id)
          .then((loaded) => {
            if (sessionRef.current?.id === next.id) {
              replaceSegments(loaded);
            }
          })
          .catch(() => {
            // The poll loop retries; keep the cleared transcript.
          });
      }
    },
    [applySession, replaceSegments],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadInitialData() {
      try {
        const loadedPeople = await listPeople();
        if (!cancelled) {
          setPeople(loadedPeople);
        }
      } catch (err) {
        if (!cancelled) {
          setErrorMessage(
            messageFromError(err, "Could not load people from the server."),
          );
        }
      }

      try {
        const sessions = await listSessions();
        if (cancelled) {
          return;
        }
        const current =
          sessions.find((candidate) =>
            ACTIVE_STATUSES.has(candidate.status),
          ) ?? null;
        if (!current) {
          return;
        }
        applySession(current);
        try {
          const loadedSegments = await getSegments(current.id);
          if (!cancelled && sessionRef.current?.id === current.id) {
            replaceSegments(loadedSegments);
          }
        } catch (err) {
          if (!cancelled) {
            setErrorMessage(
              messageFromError(err, "Could not load the transcript."),
            );
          }
        }
      } catch (err) {
        if (!cancelled) {
          setErrorMessage(
            messageFromError(err, "Could not load sessions from the server."),
          );
        }
      }
    }

    // Polling fallback: the server has no /ws/live endpoint yet (its only
    // socket is the audio ingest socket), so a WS only page would sit
    // empty forever. Poll the REST endpoints so the transcript, speaker
    // cards, and session state stay live either way, and pick up
    // sessions started from another client (for example the bridge).
    async function poll() {
      if (cancelled) {
        return;
      }
      try {
        const loadedPeople = await listPeople();
        if (!cancelled) {
          setPeople(loadedPeople);
        }
      } catch {
        // Keep the last good list; the initial load surfaces errors.
      }
      try {
        const sessions = await listSessions();
        if (cancelled) {
          return;
        }
        const current = sessionRef.current;
        if (!current) {
          const active =
            sessions.find((candidate) =>
              ACTIVE_STATUSES.has(candidate.status),
            ) ?? null;
          if (active) {
            switchToSession(active);
          }
          return;
        }
        const fresh = sessions.find((candidate) => candidate.id === current.id);
        if (fresh && fresh.status !== current.status) {
          applySession(fresh);
        }
        if (current.status === "ended") {
          return;
        }
        const loadedSegments = await getSegments(current.id);
        if (!cancelled && sessionRef.current?.id === current.id) {
          mergeSegments(loadedSegments);
        }
      } catch {
        // Keep the last good state; the next poll retries.
      }
    }

    const client = new LiveClient();

    const offEvent = client.onEvent((event: LiveEvent) => {
      const shape = event as unknown as EventShape;

      // The server sends sealed segments as a batch:
      // { type: "segments", segments: [...] }.
      if (Array.isArray(shape.segments)) {
        mergeSegments(
          shape.segments.map((item) => normalizeSegment(item)),
        );
        return;
      }

      if (shape.segment) {
        mergeSegments([normalizeSegment(shape.segment)]);
        return;
      }

      if (shape.session) {
        switchToSession(shape.session);
        return;
      }

      if (shape.type === "session_ended") {
        const current = sessionRef.current;
        if (current && (!shape.session_id || shape.session_id === current.id)) {
          applySession({ ...current, status: "ended" });
          setActiveSpeakerId(null);
        }
        return;
      }

      if (shape.type === "speaker_active") {
        setActiveSpeakerId(
          shape.person_id ??
            shape.speaker_person_id ??
            shape.speaker_id ??
            shape.active_speaker_id ??
            null,
        );
        return;
      }

      if (shape.type === "status") {
        const source = captureSourceFromRecord(
          shape as unknown as Record<string, unknown>,
        );
        if (source) {
          setCaptureSource(source);
        }
      }
    });

    const offState = client.onState((state) => {
      if (!cancelled) {
        setConnectionState(String(state));
      }
    });

    client.connect();
    void loadInitialData();
    const pollTimer = setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelled = true;
      clearInterval(pollTimer);
      offEvent();
      offState();
      client.close();
    };
  }, [applySession, mergeSegments, replaceSegments, switchToSession]);

  const talkTimeByPerson = useMemo(() => {
    const totals: Record<string, number> = {};
    for (const segment of segments) {
      if (segment.gate_status === "SILENCE") {
        continue;
      }
      if (!segment.speaker_person_id) {
        continue;
      }
      const duration = Math.max(0, segment.end_s - segment.start_s);
      if (!Number.isFinite(duration)) {
        continue;
      }
      totals[segment.speaker_person_id] =
        (totals[segment.speaker_person_id] ?? 0) + duration;
    }
    return totals;
  }, [segments]);

  function absorbActionResult(updated: Session) {
    if (updated.new_segments && updated.new_segments.length > 0) {
      mergeSegments(updated.new_segments);
    }
    const { new_segments: _dropped, ...rest } = updated;
    void _dropped;
    applySession({ ...(sessionRef.current ?? {}), ...rest } as Session);
  }

  async function handleStart() {
    setBusy(true);
    setErrorMessage(null);
    try {
      const existing = sessionRef.current;
      if (existing && existing.status !== "ended") {
        // A session already exists but its controller is not recording
        // yet (idle or armed). Start that one instead of creating a
        // duplicate session nobody is recording into.
        let current = existing;
        if (current.status === "idle" || current.status === "unknown") {
          current = await sessionAction(current.id, "arm");
          absorbActionResult(current);
        }
        if (sessionRef.current?.status === "armed") {
          const started = await sessionAction(existing.id, "start");
          absorbActionResult(started);
        }
        return;
      }
      // Creating a session on the server only creates the row and an
      // IDLE controller: nothing records until arm then start succeed,
      // and pause or end before that fail with a 409.
      const created = await createSession();
      applySession(created);
      replaceSegments([]);
      setActiveSpeakerId(null);
      const armed = await sessionAction(created.id, "arm");
      absorbActionResult(armed);
      const started = await sessionAction(created.id, "start");
      absorbActionResult(started);
    } catch (err) {
      setErrorMessage(
        messageFromError(err, "Could not start a session. Is the server up?"),
      );
    } finally {
      setBusy(false);
    }
  }

  async function runSessionAction(action: "pause" | "resume" | "end") {
    const current = sessionRef.current;
    if (!current) {
      return;
    }
    setBusy(true);
    setErrorMessage(null);
    try {
      const updated = await sessionAction(current.id, action);
      absorbActionResult(updated);
      if (action === "end") {
        setActiveSpeakerId(null);
      }
    } catch (err) {
      setErrorMessage(
        messageFromError(
          err,
          `Could not ${action} the session. Is the server up?`,
        ),
      );
    } finally {
      setBusy(false);
    }
  }

  function handlePause() {
    void runSessionAction("pause");
  }

  function handleResume() {
    void runSessionAction("resume");
  }

  function handleEnd() {
    void runSessionAction("end");
  }

  const connectionLabel =
    connectionState === "open"
      ? "connected"
      : connectionState === "closed" || connectionState === "disconnected"
        ? "disconnected"
        : connectionState;

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center justify-between gap-4 border-b px-4 py-3">
        <span className="font-semibold">OnRecord Live</span>
        <SessionControls
          session={session}
          busy={busy}
          connectionState={connectionLabel}
          onStart={handleStart}
          onPause={handlePause}
          onResume={handleResume}
          onEnd={handleEnd}
        />
      </header>

      <main className="flex min-h-0 flex-1 gap-4 p-4">
        <section className="flex min-w-0 flex-1 flex-col gap-2">
          <h1 className="text-sm font-semibold">Live transcript</h1>
          {errorMessage && (
            <p className="text-sm" role="alert">
              {errorMessage}
            </p>
          )}
          <LiveTranscript segments={segments} />
        </section>

        <aside className="w-72 shrink-0 space-y-4 overflow-y-auto">
          <div className="space-y-1 rounded border p-3 text-sm">
            <p>Live feed: {connectionLabel}</p>
            {captureSource === "phone" ? (
              <p>Phone-only fallback active</p>
            ) : (
              <p>Capture: glasses</p>
            )}
          </div>
          <SpeakerCards
            people={people}
            activeSpeakerId={activeSpeakerId}
            talkTimeByPerson={talkTimeByPerson}
          />
        </aside>
      </main>
    </div>
  );
}
