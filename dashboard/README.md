# OnRecord Dashboard

Next.js dashboard for OnRecord.

## How to run

Install dependencies:

```bash
npm install
```

Start the dev server:

```bash
npm run dev
```

The dashboard runs on port 3000: http://localhost:3000

Other scripts:

```bash
npm run build
npm run start
```

## Environment variables

Copy `.env.example` to `.env.local` and adjust if needed.

- `NEXT_PUBLIC_API_URL`: base URL for the backend REST API. Default: `http://localhost:8000`
- `NEXT_PUBLIC_WS_URL`: WebSocket URL for live events. Default: `ws://localhost:8000/ws/live`

## Backend endpoints used

REST base is `NEXT_PUBLIC_API_URL`. These are the endpoints the server
actually exposes, and the ones `lib/api.ts` calls:

- `GET /healthz`
- `GET /api/people`
- `POST /api/people/enroll` with a JSON body `{ name, photo_b64, voice_b64 }`
  (the server does not parse multipart form data)
- `GET /api/sessions`
- `GET /api/sessions/{id}` (returns the session plus its `segments`;
  there is no separate segments endpoint and no `/api/stats`, the
  status page aggregates counts from the list and detail endpoints)
- `POST /sessions` to create a session, then `POST /sessions/{id}/arm`
  and `POST /sessions/{id}/start` before it records; lifecycle actions
  are `POST /sessions/{id}/pause`, `/resume`, and `/end`, and they
  return `{ session, state, new_segments }`
- `GET /api/search?q=...&person_id=...`

Server payloads use `speaker_label`, `start_offset_s`, `end_offset_s`,
session `source`, and person `created_at`; `lib/api.ts` normalizes them
into the dashboard shapes in `lib/types.ts`.

Live updates are expected over a WebSocket at `NEXT_PUBLIC_WS_URL`,
sending JSON events. The server does not expose that endpoint yet (its
only socket is the audio ingest socket), so the live page also polls
the REST endpoints every few seconds and stays current without it.

## Styling note

Visual styling is intentionally plain pending a design template. The scaffold uses plain Tailwind utilities only, with no custom colors or design system.
