<!-- Lexia Read Mode for OnRecord. Uncompiled Swift source written Oct 2026. Bridge team: copy into the bridge-ios target and wire the marked integration points. -->

# Lexia Read Mode: Swift bridge sources

Swift sources for the bridge side of Lexia Read Mode in OnRecord.

Read Mode flow:

1. The user pinches on the Neural Band or taps "Read this" on the phone.
2. The glasses (or the phone camera as a fallback) capture one still of the text in view.
3. The still is POSTed to the standalone Lexia API on the laptop at port 8100.
4. Lexia returns reading friendly chunks.
5. Chunks are shown one at a time on the lens, with optional read aloud using the TTS audio URL Lexia returns.

Honest status: none of this is compiled or device tested yet. There is no Xcode on the machine where these files were written, and no run on the Ray-Ban Meta Display glasses or with a Neural Band has happened. Treat every file as a clean, plausible starting point that the bridge team must build, fix, and test in Xcode.

## Files

### LexiaModels.swift

Codable structs matching the Lexia API contract, plus the shared error type.

- `ReadChunk`: one chunk with `index`, `text`, and `word_count`.
- `ReadResponse`: response from `POST /read` with `session_id`, `text`, `chunks`, `mean_confidence`, and `engine`.
- `SpeakResponse`: response from `POST /read/{session_id}/speak` with `audio_url`, `duration_seconds`, and `engine`.
- `HealthResponse`: response from `GET /health` with `status`, `ocr_engine`, and `tts_engine`.
- `LexiaError`: cases `badURL`, `network`, `server(status:)`, `decoding`, and `noSession`, with `LocalizedError` descriptions.

### LexiaAPIClient.swift

`final class LexiaAPIClient` built on `URLSession`.

- Configurable `baseURL`, set at init or changed later.
- `health()` calls `GET /health`.
- `read(imageData:simplify:)` base64 encodes JPEG data, POSTs JSON to `/read`, and returns a `ReadResponse`.
- `speak(sessionID:)` POSTs to `/read/{session_id}/speak` and returns a `SpeakResponse`. An empty session id throws `LexiaError.noSession`.
- `audioURL(for:)` resolves a `SpeakResponse` audio URL against the base URL, handling both absolute URLs and relative paths.
- Non 2xx responses throw `LexiaError.server(status:)`. Transport failures throw `.network`. JSON failures throw `.decoding`.

### ReadCapture.swift

Capture abstraction, stubs, and trigger handling.

- `ReadCaptureSource` protocol: `captureStill() async throws -> Data` returning JPEG data.
- `PhoneCameraCaptureStub`: stub only. It throws `ReadCaptureError.notImplemented`. The bridge team wires AVFoundation capture here.
- `GlassesCaptureStub`: stub only. It throws `ReadCaptureError.notImplemented`. The bridge team wires Meta Wearables DAT camera still capture here, reusing the bridge app session.
- `ReadCaptureError`: `notImplemented`, `noImageData`, and `permissionDenied`.
- `ReadTrigger` protocol with `triggerPinch()` and `triggerTap()`.
- `ReadTriggerHandler`: concrete handler with `onPinch` and `onTap` closures and 1.5 second debounce. Triggers inside the window are ignored, so a band double fire plus a phone tap do not start two captures.

### LensChunkDisplayModel.swift

`@MainActor final class LensChunkDisplayModel: ObservableObject` for the lens.

- Published `chunks`, `currentIndex`, `currentChunk`, and `isSpeaking`, plus the loaded `sessionID`, `fullText`, and `meanConfidence`.
- `load(_:)` loads a `ReadResponse`, sorts chunks by index, and resets to the first chunk.
- `next()` and `previous()` move one chunk and stop at the ends.
- `positionLabel` gives the one based label, for example "3 of 12".
- `estimatedSeconds(for:)` returns `word_count / 2.6`, about 156 words per minute. It is a pacing estimate only. Prefer `SpeakResponse.durationSeconds` for real audio length.
- Design rule: exactly one chunk is visible at a time.

### ReadModeView.swift

SwiftUI skeleton view using `LensChunkDisplayModel` and `LexiaAPIClient`.

- States enum: `idle`, `capturing`, `loading`, `reading`, and `error(String)`.
- Shows the current chunk as large text with the position label "3 of 12" format.
- Previous and Next buttons call the display model.
- "Read this" button starts the capture flow through the trigger handler when one is provided.
- "Read Aloud" button calls `speak(sessionID:)` and resolves the audio URL. Playback itself is not implemented in this file.
- Only system default styling is used. No custom design is applied.

## API contract

Base URL example: `http://<laptop>:8100`, where `<laptop>` is the laptop IP on the demo network.

- `POST /read`
  - Request JSON: `{"image_base64": str, "simplify": bool}`
  - Response JSON: `{"session_id", "text", "chunks": [{"index", "text", "word_count"}], "mean_confidence", "engine"}`
- `POST /read/{session_id}/speak`
  - Response JSON: `{"audio_url", "duration_seconds", "engine"}`
- `GET /health`
  - Response JSON: `{"status", "ocr_engine", "tts_engine"}`

## Integration checklist for the bridge team

- [ ] Copy all five Swift files into the `bridge-ios` target and add them to the target in Xcode. Resolve any compile errors there, since these files have never been compiled.
- [ ] Set the laptop base URL. Create one shared `LexiaAPIClient` with the laptop address on the demo network, for example `http://192.168.1.20:8100`. Do not use `localhost` from the phone. Confirm `GET /health` succeeds from the phone on that network before demo day.
- [ ] Wire DAT still capture. Replace the throw in `GlassesCaptureStub.captureStill()` with Meta Wearables DAT camera still capture, convert the result to JPEG `Data`, and reuse the bridge app DAT session instead of creating a second one.
- [ ] Wire the phone fallback if wanted. Replace the throw in `PhoneCameraCaptureStub.captureStill()` with AVFoundation photo capture, including permission handling and JPEG conversion.
- [ ] Wire the Neural Band pinch to `ReadTriggerHandler`. Call `triggerPinch()` from the band input handler in the bridge app, call `triggerTap()` from the phone "Read this" button, share one handler instance, and set `onPinch` and `onTap` to start the same capture flow.
- [ ] Wire audio playback. In `ReadModeView.readAloud()`, at the marked integration point, play the resolved `audio_url` with `AVAudioPlayer` or `AVPlayer`, route audio to the glasses speaker if that is the demo plan, and clear `isSpeaking` when playback ends or fails.
- [ ] Decide the lens rendering path. This view is the phone facing skeleton. Mirror `LensChunkDisplayModel.currentChunk` and `positionLabel` into the actual lens surface the bridge app uses for the Display glasses, keeping the one chunk at a time rule.
- [ ] Add the local network permission and any camera usage description strings the bridge target needs in its Info.plist, and confirm the Lexia laptop firewall allows inbound port 8100.
- [ ] Test end to end on hardware: pinch trigger, glasses still capture, `/read` round trip on demo WiFi, chunk navigation, and read aloud playback. None of that has been tested yet.

## What is stubbed

- Phone camera capture: stub, throws `notImplemented`.
- Glasses camera capture: stub, throws `notImplemented`.
- Neural Band input: not implemented here by design. Only the handler and debounce are provided.
- Audio playback: not implemented. Only the `speak` request and URL resolution are provided.
- Lens rendering beyond the SwiftUI skeleton: not implemented. The display model is the shared piece.
- Styling: none beyond SwiftUI system defaults.
