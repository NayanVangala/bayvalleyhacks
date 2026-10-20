"use client";

import { useEffect, useRef } from "react";

import { formatClock } from "../lib/api";
import type { Segment } from "../lib/types";

export default function LiveTranscript({ segments }: { segments: Segment[] }) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nearBottomRef = useRef(true);

  useEffect(() => {
    const container = containerRef.current;
    if (container && nearBottomRef.current) {
      container.scrollTop = container.scrollHeight;
    }
  }, [segments.length]);

  function handleScroll() {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const distanceFromBottom =
      container.scrollHeight - container.scrollTop - container.clientHeight;
    nearBottomRef.current = distanceFromBottom <= 80;
  }

  if (segments.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto rounded border p-3">
        <p className="text-sm">No segments yet. Start a session and speak.</p>
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      onScroll={handleScroll}
      className="flex-1 overflow-y-auto rounded border p-3"
    >
      <ul className="space-y-2">
        {segments.map((segment) => {
          const isSilence = segment.gate_status === "SILENCE";
          const text = isSilence && !segment.text ? "(silence)" : segment.text;
          return (
            <li key={segment.id} className="flex items-start gap-2 text-sm">
              <span className="shrink-0 px-1 py-1">
                {formatClock(segment.start_s)}
              </span>
              <span className="shrink-0 px-1 py-1 font-bold">
                {segment.speaker_name || "Unknown"}
              </span>
              <span className="shrink-0 rounded border px-2 py-1 text-xs">
                {segment.gate_status}
              </span>
              <span className={isSilence ? "px-1 py-1 italic" : "px-1 py-1"}>
                {text}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
