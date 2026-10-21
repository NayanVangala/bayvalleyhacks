# InSight Lessons

Source: reverse-engineered notes on the InSight repo (Cal Hacks 12.0 winner, Oct 2025),
applied to our stack. Full technique-by-technique analysis lives in the
`insight-glasses-fix` workflow notes (`/tmp/insight-applied.md` on the build VM);
this doc is the permanent record of what we kept.

Date: 2026-10-20

## What InSight built

In Oct 2025 there was no public glasses SDK, so InSight captured audio/video with a
transport hack:

- Video: stream from the glasses to Instagram Live via Meta View, open that Live in
  a Mac browser, then `ffmpeg -f avfoundation -i "1:0" -vf crop=W:H:X:Y` to
  screen-crop the browser window. Calibration helper: an interactive
  Cmd+Shift+4 coordinate script for the crop box.
- Audio: route the stream through a BlackHole multi-output device so FFmpeg could
  capture it, then re-encode AAC to 16 kHz mono WAV for processing.
- Segmentation: VAD-gated FFmpeg segments, 3 to 60 s each, with 0.5 s pre/post-roll
  padding. In practice the live path ran a bare RMS-energy check (`energy > 0.02`)
  and at demo time fell back to MANUAL mode (a human pressed keys when people
  talked).
- Transcription: faster-whisper `base`, CPU, int8, beam 5, word timestamps.
- Speaker identity: pyannote segmentation plus `pyannote/speaker-diarization-3.1`
  with `num_speakers=2` hardcoded, and wespeaker embeddings averaged over the
  wearer's 3 longest segments, compared against a reference clip.
- Face recognition: dlib HOG + 68-point landmarks + 128-d encoding, euclidean
  threshold 0.5, run on every 30th frame. Their "lip-detection fusion" from the
  Devpost was dead code; the real mapping was Speaker 1 to wearer, Speaker 2 to
  the first recognized face.
- Keep-alive: none. No reconnect, no retry, no stream health checks. "Handle
  stream interruptions" was literally listed under Future Enhancements. Resilience
  was a graceful FFmpeg shutdown (`q`, wait 2 s, SIGTERM, SIGKILL) and a 60 s
  max-segment rotation timer.
- Secrets: Supabase credentials hardcoded in `database.py`.

## Obsolete with Meta Wearables Device Access Toolkit 1.0

Toolkit 1.0 gives us native capture, so these techniques are dead to us:

- Instagram Live + FFmpeg screen-crop. Replaced by `GlassesSource.swift`: one
  `StreamSession` held open for the whole conversation, `videoFramePublisher`
  for frames, `capturePhoto()` for stills. No browser, no crop box, no extra hop.
- BlackHole audio routing for capture. We never capture from the laptop, so the
  whole macOS system-audio problem does not exist in our pipeline.
- Fragile FFmpeg device indexes (`:0` vs `:1`). Not our failure surface.
- dlib HOG face recognition. We run ArcFace with dimension-checked, renormalized
  512-d embeddings. Do not revisit dlib.
- Hardcoded `num_speakers=2` diarization. Our identity path is enrollment-based
  and degrades honestly to Unknown.
- Their "Speaker 1 is the wearer" fusion hack and dead-code lip detection.
  Their Devpost claimed fusion that did not exist in the code; our demo contract
  (deterministic claims only, exact-match receipts, semantic search banned from
  the demo) exists to prevent exactly this failure mode.

What survives from their transport work as discipline, not code: they measured
real numbers with crude scripts (`setup_screen_region.py`,
`test_ffmpeg.py`). We do the same on hardware day: photo trigger-to-bytes
latency at each resolution, shutter contamination window, audio format assert,
weak-link walk test. See `docs/HARDWARE-DAY.md`.

## Failure modes that still apply to us

These bit InSight; each has a live counterpart in our stack:

1. Wrong audio source. They discovered mid-event they were recording the laptop
   mic instead of the Live audio. Our analog: the glasses mic is experimental and
   the HFP 8 kHz trap. Answer: phone mic is the deterministic path,
   `GlassesSource` converts to PCM16 16 kHz mono and drops buffers that fail,
   plus the runtime 16 kHz assert on setup day. Do not skip that check.
2. Zero reconnect handling. They shipped with no keep-alive and unsolved drops.
   We must not regress: `WSClient` ping every 15 s with drop on no-pong over 30 s,
   backoff with jitter while the session is live, `OfflineStaging` with disk
   ring replay oldest-first after re-hello, and server-side dedupe on reconnect
   (`server/onrecord/ingest.py`). This needs a real kill-and-replay drill,
   including a Wi-Fi-drop-during-replay variant.
3. Format drift at capture time. Their device-index fragility is our format and
   transport fragility. Keep the Info.plist transport freeze (no runtime
   transport picker, and note their fiddly crop-box calibration died under
   event pressure too), keep per-frame header validation in `ingest.py`
   (bad magic or unknown kind drops the frame, socket stays open).
4. Permission gating. They lost time to macOS Screen Recording permissions that
   needed a Terminal restart. Our version: iOS mic/camera/local-network prompts
   and Meta AI registration. Pre-authorize everything on the demo iPhone before
   event day; register once via universal link, never at the venue. No fresh
   installs on Oct 25.
5. Secrets in source. They shipped Supabase creds in `database.py`. Ours are the
   Meta `ClientToken` and server env vars. ClientToken goes via build variables,
   never hardcoded. Grep the repo before event day to confirm nothing leaked.
6. Thermal and CPU. Their continuous video ran hot; we ban video and use stills
   only, with 15 to 20 minute cooldowns and no charging while hot.
7. Faked automation. Their "automatic" segmentation was a human pressing keys.
   Our analog: push-to-talk is the deterministic trigger, VAD is OFF by design
   (whisper-side VAD would double-gate and drop speech). The PTT path must be
   genuinely tested end-to-end, including the zero-padding bug flagged in
   `REVIEW-REPORT.md`; simulated button presses do not count.
8. Re-pairing and session churn. They fought re-pairing; we hold one session open
   for the whole conversation (`GlassesSource.start()` is a no-op if already
   started), and source failover (glasses to phone) is explicitly NOT a reconnect:
   socket, session ID, `frameSeq`, and session clock all carry on. If pairing
   breaks day-of, we are phone-only by plan, per `fallback-mode.md`.
9. Enrollment sparsity. Their logs show the wearer repeatedly unrecognized from a
   small enrollment set at a fixed threshold. Enroll each demo speaker once, on
   setup day, with multiple clips (see Steals below).

## What we stole

1. Multi-clip enrollment averaging. Their "average the embeddings of the 3
   longest segments" trick for the wearer reference. Apply to our voice
   enrollment: average ECAPA embeddings over 2 to 3 clips instead of one, and do
   the same for face if cheap. Targets: `server/onrecord/voice.py` enrollment
   path, plus `docs/ENROLLMENT-GUIDE.md` telling Nayan to record 2 to 3 short
   clips per speaker on setup day.
2. Unknown-face persistence. They saved unrecognized faces to
   `video/unknown_faces/`. We should persist unknown face crops server-side at
   match time (crop plus `captureTsMs`) and surface the queue in the dashboard
   for one-tap enrollment, so setup day can enroll-then-retry without re-running
   the demo. Target: `server/onrecord/face.py`, plus a dashboard queue. Our
   Unknown fallback is by design; this keeps the evidence so Nayan can fix it
   in one tap.
3. Server-side runaway-segment force-seal. Their 60 s max-segment rotation timer.
   Our segments are PTT-bracketed (`talk_start` / `talk_end`), but a crash
   between the two leaves an unsealed segment. Add a force-seal: any segment
   open with no frames for over 5 minutes is sealed as gap-terminated instead
   of growing unbounded. Targets: `server/onrecord/state_machine.py` /
   `ingest.py` segment-seal path.
4. Hardware-day measurement script. In the spirit of their crude but honest
   `setup_screen_region.py` / `test_ffmpeg.py`: make `docs/HARDWARE-DAY.md` a
   runnable script that prints photo trigger-to-bytes latency, the audio format
   assert result, the shutter contamination window, and weak-link results to a
   log Nayan can read at the table.
5. No swallowed errors on the capture path. Their FFmpeg stderr watcher is the
   ancestor of `GlassesSource.forwardError` turning every SDK error into a
   health snapshot. Keep the discipline everywhere.

## Validation, not reinvention

Their segment + pre-roll + faster-whisper shape survived hackathon pressure, and
it matches ours: PTT-bracketed segments with 10 s pre-roll (more generous than
their 0.5 s pad, covering the judge who talks before the button), faster-whisper
with a stricter pinned decode contract (`small.en`, int8, beam 5, temperature
0.0, language locked to "en" and asserted), and confidence-gated VERIFIED /
REVIEW / SILENCE. The fake transcriber is now a hard error unless
`ONRECORD_STT=fake` is explicit, closing the analog of their manual-mode
dishonesty. No architecture change: Toolkit 1.0 native capture plus our WebSocket
protocol plus the phone-only fallback is strictly superior to everything they
built, and our keep-alive story covers the outage class they shipped with zero
handling.

The two Swift blockers from `REVIEW-REPORT.md` (phone must `POST /sessions`
before the server accepts the session ID; enrollment endpoint/encoding mismatch)
remain the critical path and are untouched by these findings.
