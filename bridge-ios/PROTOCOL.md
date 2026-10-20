# InsightBridge wire protocol

This is the full wire spec between the InsightBridge iOS app and the
laptop server, as implemented in `Net/WireProtocol.swift`. If the code
and this document ever disagree, treat it as a bug in one of them and
fix it before the demo: the server is built against this document.

## 1. Endpoint and transport

- One WebSocket connection per session.
- URL: `ws://<host>:8000/ingest/audio?session_id=<uuid>`
- Default host: `192.168.2.1`, the laptop hosting the Wi-Fi network
  `insight-net`. The session ID in the query string is created by the
  phone when the session is armed, and it survives reconnects and
  capture-source failover. One session ID, one conversation.
- Two frame kinds on the one connection:
  - Text frames carry JSON control messages (section 3).
  - Binary frames carry audio or photo payloads, each prefixed with
    the 16-byte binary header (section 4). There are no JSON metadata
    sidecar frames for binary payloads: everything the server needs is
    in the header.

## 2. Handshake

1. The phone opens the WebSocket with the session ID in the query
   string.
2. The first frame the phone sends is a `hello` text frame. The
   client does not block streaming on the handshake: binary frames
   may flow immediately after `hello`.
3. The server replies with a `welcome` text frame, used for clock
   offset estimation and connection confirmation.
4. If the server cannot accept the session, it replies with an `error`
   frame with `fatal: true` and closes the connection.
5. On a reconnect for the same session, the phone sends `hello` again
   with the same session ID (in the query string and in the message).
   The server treats it as a resume, not a new session.

## 3. Control messages

All control messages are JSON objects in WebSocket text frames. Every
message has a `type` field. Field names are snake_case on the wire.
Unknown fields must be ignored by both sides. Unknown message types
must be ignored, not treated as errors.

### 3.1 Client to server

#### hello

Sent first on every connect and reconnect.

```json
{
  "type": "hello",
  "protocol": 1,
  "app": "insight-bridge",
  "app_version": "1.0",
  "session_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "source": "phone",
  "audio_format": { "codec": "pcm16", "sample_rate": 16000, "channels": 1 }
}
```

- `source` is `phone` or `glasses`: the capture source active at
  connect time. It can change later without a new hello (see the
  failover note in section 6).
- `protocol` is the wire protocol version. This document is version 1.

#### talk_start

Sent when a push-to-talk segment begins, before the segment's first
audio frame. The pre-roll audio drained at talk start is part of the
segment and is sent as ordinary audio frames for this `seg_idx`,
flagged with the PREROLL bit (section 5).

```json
{
  "type": "talk_start",
  "seg_idx": 0,
  "t0_mono_ms": 15230
}
```

- `seg_idx`: the segment index. Starts at 0 for the first segment of a
  session and increments by 1 after each completed segment. It matches
  the `segIdx` field in the binary header of every frame in the
  segment and the `segmentIndex` in the session state machine.
- `t0_mono_ms`: segment start time on the session clock (section 8),
  in milliseconds. For a segment that begins with pre-roll, this is
  the time the pre-roll starts, not the time the button was pressed.

#### talk_end

Sent when a push-to-talk segment ends, after the segment's last audio
frame.

```json
{
  "type": "talk_end",
  "seg_idx": 0,
  "t1_mono_ms": 21480,
  "duration_ms": 6250
}
```

#### session_end

Sent once when the session ends. The phone closes the WebSocket after
sending it.

```json
{
  "type": "session_end",
  "reason": "user"
}
```

- `reason` is one of `user`, `error`. Statistics the server needs are
  derivable from the frames it received; do not invent extra fields.

#### ping

Sent periodically while the session is live, at least every 15 s, to
keep the connection warm and to track clock offset (section 8).

```json
{
  "type": "ping",
  "t0_mono_ms": 30000
}
```

#### session_start

Sent once after the handshake, when the session is armed on the
phone. Fields: `session_id`, `started_at` (ISO 8601 wall clock),
`talk_mode` (`push_to_talk`).

#### session_pause and session_resume

Sent when the wearer pauses or resumes the session. Each carries
only `session_id`. A pause seals the open segment server side and
opens an explicit pause gap record on the session timeline; a
resume closes the gap. Segments never span a pause.

#### photo_meta

Sent immediately before each photo binary frame (the eagerness
declaration referenced in section 5.2). Fields: `session_id`,
`photo_id`, `seq` (the photo sequence, see section 5.2),
`captured_at` (ISO 8601 wall clock), `eager` (bool), `bytes`
(JPEG length). The binary frame itself is self describing; this
message is timing context only.

#### tag_moment

A wearer bookmark for later lookup. Fields: `session_id`,
`t_mono_ms` (session clock), `source` (the input that produced the
tag). Pure control message; capture is undisturbed.

#### Legacy control names

The server also accepts the REST-era control vocabulary on the same
socket (`arm`, `start`, `new_segment`, `talk_stop`, `pause`,
`resume`, `photo`, `end`, with the legacy binary header described
in the server module docstring). New bridge clients use the
messages above; the legacy names exist so older clients and the
server test suite keep working.

### 3.2 Server to client

Server messages are parsed by `ServerMessage.parse(_:)` in the app,
which recognizes `welcome`, `pong`, `capture_now`, and `error`, and
returns nil for anything else.

#### welcome

Reply to `hello`. Confirms the session and gives the phone the server
clock reading used for the first offset estimate.

```json
{
  "type": "welcome",
  "session_id": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
  "server_time": "2026-10-25T18:02:11.050Z",
  "ok": true
}
```

#### pong

Reply to `ping`. Echoes the phone's timestamp so the phone can pair
the reply with its send time.

```json
{
  "type": "pong",
  "t0_mono_ms": 30000,
  "server_time": "2026-10-25T18:02:41.050Z"
}
```

#### capture_now

The server asks the phone to take one photo immediately, outside the
normal 2.5 s cadence. The photo is sent as an ordinary photo binary
frame. `reason` is informational only.

```json
{
  "type": "capture_now",
  "reason": "operator_request",
  "photo_id": "9b2f4c1a-7d3e-4e5f-8a6b-1c2d3e4f5a6b"
}
```

#### error

```json
{
  "type": "error",
  "code": "UNKNOWN_SESSION",
  "message": "no such session",
  "fatal": false
}
```

- `code` is a machine-readable string. Known codes: `BAD_PROTOCOL`,
  `UNKNOWN_SESSION`, `SERVER_BUSY`.
- `fatal: true` means the server is about to close the connection and
  the session cannot continue on it. `fatal: false` is a warning; the
  session continues.

#### segments and session_ended (server extension)

The app does not parse these; they exist for other clients of the
same socket (the dashboard and the server test suite), and the app
ignores them like any other unrecognized message.

- `segments` is sent after a batch of segments is sealed and
  processed: `{"type": "segments", "segments": [...], "gaps": [...]}`.
  The `gaps` array carries the explicit timeline gap records closed
  since the previous batch (pauses, frame gaps, reconnects), each
  with the session offset at which it starts.
- `session_ended` is sent once after `session_end` (or the legacy
  `end`) has been processed and the session is closed server side:
  `{"type": "session_ended"}`.

## 4. Binary frame header

Every binary WebSocket frame starts with a 16-byte header, built by
`BinaryFrameHeader.encode()` and read by `BinaryFrameHeader.decode(_:)`.
All multi-byte integers are big endian. The header is exactly 16 bytes;
there is no length field, because the payload length is the WebSocket
frame length minus 16.

| Offset | Size | Field | Type | Notes |
| ------ | ---- | ----- | ---- | ----- |
| 0 | 1 | magic byte 0 | UInt8 | Always 0x4F, the letter O |
| 1 | 1 | magic byte 1 | UInt8 | Always 0x52, the letter R |
| 2 | 1 | kind | UInt8 | 1 = audio, 2 = photo. Other values are invalid |
| 3 | 1 | flags | UInt8 | Bit flags, see section 5.3 |
| 4 | 4 | segIdx | UInt32, big endian | Segment index this frame belongs to |
| 8 | 4 | frameSeq | UInt32, big endian | Frame counter, see section 5.2 |
| 12 | 4 | captureTsMs | UInt32, big endian | Capture time on the session clock, ms, see section 8 |

Validation on decode: the data must be at least 16 bytes and the two
magic bytes must match, or decode returns nil and the frame is dropped.
A conforming sender always produces exactly 16 header bytes; a frame
shorter than 16 bytes total is malformed and must be dropped whole.

Worked example: an audio frame with flags 0, segIdx 0x01020304,
frameSeq 1, captureTsMs 1000 encodes its header as:

```
4F 52 01 00  01 02 03 04  00 00 00 01  00 00 03 E8
```

## 5. Frame rules

### 5.1 Audio frames

- Format: PCM16, 16 kHz, mono, little endian samples. No WAV header on
  the wire; the payload is raw PCM. (WAV wrapping exists only for
  enrollment uploads, section 7.)
- Payload size: exactly 3200 bytes per frame, which is 100 ms of
  audio. Senders chunk the capture stream into 100 ms frames; the
  server may rely on the size.
- Exception: the last audio frame of a segment may be shorter than
  3200 bytes, holding only the samples actually captured. Its length
  is the frame length minus 16. Receivers must not assume padding.
- Audio flows only inside a segment: between `talk_start` and
  `talk_end` for that `seg_idx`, including the pre-roll audio drained
  at talk start.

### 5.2 Photo frames and counters

- Format: one complete JPEG per binary frame. No chunking: a photo is
  either one whole frame or it is not sent.
- Encoding: 960 px on the long edge, JPEG quality 0.7.
- Cadence: one photo every 2.5 s while a session is live, plus one
  eager photo at session start and at every talk start, plus one
  whenever the server sends `capture_now`. Eagerness is declared in
  the `photo_meta` control message, not in the frame header.
- Photos are not tied to a segment: a photo frame carries the
  sentinel `segIdx` value 0xFFFFFFFF. Its ordering anchor is
  `captureTsMs`.
- `frameSeq` for audio is per segment: it starts at 0 for the first
  frame of a segment and increments by 1 per frame within that
  segment. Photos use their own session-wide photo sequence in the
  same field. The server orders a segment's frames by `frameSeq`
  and orders frames across segments and reconnects by `segIdx` plus
  `captureTsMs`.
- Order on the wire within a segment: `talk_start`, then the
  segment's frames in `frameSeq` order, then `talk_end`.

### 5.3 Flags bits

| Bit | Mask | Name | Meaning |
| --- | ---- | ---- | ------- |
| 0 | 0x01 | PREROLL | Audio frame drained from the pre-roll buffer at segment start (audio only) |
| 1 | 0x02 | FINAL | Last frame of a segment (audio only) |
| 2 to 7 | 0xFC | reserved | Senders must set 0. Receivers must ignore |

Flags are informational. A receiver must accept a frame with any
combination of the defined bits, and must never reject a frame only
because a flag is unexpected.

## 6. Reconnect, backoff, offline staging, replay

- Drop detection: WebSocket close or error, or no `pong` within 30 s
  of a `ping`.
- Backoff: reconnect after 1 s, then 2 s, 4 s, 8 s, capped at
  16 s, with up to 25 percent random jitter. Keep retrying for as long
  as the session is live; there is no attempt limit during a session.
- While disconnected, capture does not stop. Every binary frame that
  cannot be sent is written to OfflineStaging, a 500 MB disk ring on
  the phone, together with the control messages that bracket it, in
  send order. When the ring is full, the oldest staged data is evicted
  first and the eviction counter increments once per evicted frame.
  The counter is visible in the app and is never silently reset.
- Replay order: on reconnect, complete the handshake (section 2), then
  replay staged data oldest first, in original `frameSeq` order,
  keeping the original headers (original `segIdx` and `captureTsMs`)
  unchanged. Control messages replay at their original positions in
  the stream. Live frames are queued behind replay: no live frame is
  sent until the staging ring has drained, so the server sees one
  ordered stream.
- Duplicates: if the connection drops mid-frame, the server may see a
  frame twice across the reconnect (once live, once replayed). Frames
  are identified by session ID plus `segIdx`, `frameSeq` timeline and
  `captureTsMs`; the server dedupes on that identity. The phone
  prefers replaying a possibly-duplicate frame over losing it.
- Capture-source failover (glasses to phone) is not a reconnect. The
  WebSocket, session ID, `frameSeq` and the session clock all carry
  on; only the source of new frames changes.

## 7. Enrollment endpoint

Enrollment is plain HTTP, separate from the WebSocket.

- Request: `POST http://<host>:8000/enroll` with content type
  `multipart/form-data`. Default host is the same 192.168.2.1.
- Form fields, all three required:
  - `name`: text, the person's display name.
  - `photo`: file, one JPEG portrait (same JPEG settings as photo
    frames: 960 px long edge is fine, quality 0.7).
  - `voice`: file, one WAV clip of the person speaking. The WAV is
    produced by `EnrollmentAPI.wavWrapPCM(_:sampleRate:)` from PCM16
    audio: a canonical 44-byte WAV header (RIFF/WAVE, PCM format 1,
    mono, 16 bits per sample, byte rate = sampleRate times 2) followed
    by the raw PCM. Record at 16 kHz to match the live pipeline.
- Success: a 2xx response with a JSON body containing the created
  `person_id` and the stored `name`.
- Failure: a non-2xx response with a JSON error body (`code`,
  `message`, same shape as the WebSocket `error` message). A failed
  enrollment stores nothing: all three fields are validated before
  anything is written.
- Enrollment is idempotent by name only by operator discipline, not by
  protocol: enrolling the same name twice creates two records. Enroll
  each demo speaker once, on the setup day.

## 8. Clock model

- There is one timeline that matters for ordering: the session clock.
  It starts at 0 when the session is armed on the phone and counts
  milliseconds from the phone's monotonic clock. It never goes
  backwards and is unaffected by wall-clock changes, time zones, or
  reconnects.
- `captureTsMs` in every binary header is the frame's capture time on
  the session clock: milliseconds since session start, as a UInt32.
  That wraps after about 49.7 days, which no session will reach.
- `t0_mono_ms` in `talk_start` and `ping` is on the same session clock.
  Segment start times, frame timestamps, and photo timestamps are
  therefore directly comparable with no conversion.
- Wall clock exists only for display and for the server offset
  estimate. The phone estimates the offset between its session clock
  and the server clock from `welcome` and each `pong`: the server
  stamps its wall time (`server_time`, ISO 8601 UTC) on both, and the
  phone pairs each stamp with the echoed `t0_mono_ms` and its local
  receive time to compute the offset and the round trip time. The
  phone keeps the most recent offset estimate and uses it to translate
  `captureTsMs` into server wall time when it needs to. Ordering never
  depends on this estimate; if the offset looks wrong, timestamps are
  still monotonic and segments still line up by `segIdx`.
