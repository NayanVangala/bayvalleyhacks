export type GateStatus = "VERIFIED" | "REVIEW" | "SILENCE";

export interface Person {
  id: string;
  name: string;
  enrolled_at?: string;
  created_at?: string;
  notes?: string;
  segment_count?: number;
  talk_time_s?: number;
}

export interface Session {
  id: string;
  title?: string;
  status: string;
  started_at?: string;
  ended_at?: string | null;
  capture_source?: "glasses" | "phone";
  source?: string;
  segment_count?: number;
  new_segments?: Segment[];
}

export interface Segment {
  id: string;
  session_id: string;
  seq: number;
  speaker_person_id: string | null;
  speaker_name: string;
  speaker_label?: string;
  text: string;
  gate_status: GateStatus;
  attribution_source?: string;
  start_s: number;
  end_s: number;
  start_iso?: string;
}

export interface QueryHit {
  segment_id: string;
  session_id: string;
  speaker_name: string;
  speaker_person_id: string | null;
  text: string;
  start_iso: string;
  end_iso: string;
  score?: number | null;
}

export interface QueryResult {
  status: "OK" | "EMPTY";
  reason: string | null;
  results: QueryHit[];
}

export interface Stats {
  people?: number;
  sessions?: number;
  segments?: number;
  collections?: Record<string, number>;
}

export type LiveEvent =
  | { type: "segment"; segment: Segment }
  | { type: "segments"; segments: Segment[] }
  | { type: "session"; session: Session }
  | { type: "session_ended"; session_id?: string }
  | { type: "speaker_active"; person_id: string | null }
  | { type: "status"; capture_source: "glasses" | "phone"; state?: string }
  | { type: "error"; message?: string }
  | { type: "ping" };
