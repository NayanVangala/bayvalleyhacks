import { formatTime } from "@/lib/api";
import type { Session } from "@/lib/types";

type SessionControlsProps = {
  session: Session | null;
  busy: boolean;
  connectionState: string;
  onStart: () => void;
  onPause: () => void;
  onResume: () => void;
  onEnd: () => void;
};

export default function SessionControls(props: SessionControlsProps) {
  const { session, busy, connectionState, onStart, onPause, onResume, onEnd } =
    props;

  const isEndedOrIdle =
    !session ||
    session.status === "ended" ||
    session.status === "idle" ||
    session.status === "ready";
  const isActive =
    session !== null &&
    (session.status === "recording" || session.status === "live");
  const isPaused = session !== null && session.status === "paused";
  const isArmed = session !== null && session.status === "armed";
  // Any other status (unknown, armed handled above, or a state the
  // server adds later) must still offer a wired control: without this
  // fallback the panel renders no buttons at all and the session can
  // neither be started nor ended from here.
  const isOther =
    session !== null && !isEndedOrIdle && !isActive && !isPaused && !isArmed;

  return (
    <div className="border px-4 py-3 flex flex-wrap items-center gap-4">
      <div className="flex gap-2">
        {isEndedOrIdle && (
          <button
            type="button"
            onClick={onStart}
            disabled={busy}
            className="border rounded px-3 py-1 disabled:opacity-50"
          >
            Start session
          </button>
        )}
        {isArmed && (
          <>
            <button
              type="button"
              onClick={onStart}
              disabled={busy}
              className="border rounded px-3 py-1 disabled:opacity-50"
            >
              Start recording
            </button>
            <button
              type="button"
              onClick={onEnd}
              disabled={busy}
              className="border rounded px-3 py-1 disabled:opacity-50"
            >
              End session
            </button>
          </>
        )}
        {isActive && (
          <>
            <button
              type="button"
              onClick={onPause}
              disabled={busy}
              className="border rounded px-3 py-1 disabled:opacity-50"
            >
              Pause
            </button>
            <button
              type="button"
              onClick={onEnd}
              disabled={busy}
              className="border rounded px-3 py-1 disabled:opacity-50"
            >
              End session
            </button>
          </>
        )}
        {isPaused && (
          <>
            <button
              type="button"
              onClick={onResume}
              disabled={busy}
              className="border rounded px-3 py-1 disabled:opacity-50"
            >
              Resume
            </button>
            <button
              type="button"
              onClick={onEnd}
              disabled={busy}
              className="border rounded px-3 py-1 disabled:opacity-50"
            >
              End session
            </button>
          </>
        )}
        {isOther && (
          <button
            type="button"
            onClick={onEnd}
            disabled={busy}
            className="border rounded px-3 py-1 disabled:opacity-50"
          >
            End session
          </button>
        )}
      </div>

      {session && (
        <span>
          Session {session.id.slice(0, 8)}: {session.status}
          {session.started_at
            ? `, started ${formatTime(session.started_at)}`
            : ""}
        </span>
      )}

      {session?.capture_source === "phone" && (
        <span className="border rounded px-2 py-1">
          Phone-only fallback active
        </span>
      )}
      {session?.capture_source === "glasses" && <span>Capture: glasses</span>}

      <span>
        {"●"} Feed: {connectionState}
      </span>
    </div>
  );
}
