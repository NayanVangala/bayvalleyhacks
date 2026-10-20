# InsightBridge (iOS bridge app)

InsightBridge is the iPhone companion app for the OnRecord conversation
memory system (Bay-Valley Hacks, Oct 25). The Ray-Ban Meta Display glasses
capture the conversation, this app is the pipe, and the laptop server does
the thinking.

What the bridge does:

- Captures audio and photos from the glasses through the Meta Wearables
  Device Access Toolkit (DAT), or from the phone itself in phone-only mode.
- Frames everything with the wire protocol in PROTOCOL.md and streams it
  over a local Wi-Fi WebSocket to the laptop server at
  `ws://192.168.2.1:8000/ingest/audio`.
- Runs push-to-talk with a 10 s pre-roll buffer, so the start of a
  sentence is never clipped.
- Handles speaker enrollment uploads (name, photo, voice sample).
- Stages frames on disk when the connection drops and replays them on
  reconnect, so a Wi-Fi hiccup never loses conversation data.

What it does not do: no transcription, no face or voice recognition, no
query UI on the phone. All of that lives on the server. The bridge is a
dumb, reliable pipe on purpose.

## Layout

```
bridge-ios/
  README.md                  this file
  PROTOCOL.md                the full wire spec, as implemented
  InsightBridge/
    InsightBridge.xcodeproj  open this on the Mac
    InsightBridge/
      App/                   app entry point, DAT registration bootstrap
      Assets.xcassets/       app icon and assets
      Common/                SessionState.swift (conversation state machine)
      Enrollment/            EnrollmentAPI.swift (WAV wrap, /enroll upload)
      Net/                   WireProtocol.swift (binary header, control
                             messages), WebSocket client
      Session/               SessionManager, PTTController, PreRollBuffer,
                             OfflineStaging
      Sources/               CaptureSource protocol and its implementations:
                             GlassesSource, PhoneSource, MockSource
      UI/                    SwiftUI screens (pairing, session, enrollment)
    InsightBridgeTests/
      WireProtocolTests.swift    header, control and server messages,
                                 plus WAVWrapTests (see note below)
      PreRollBufferTests.swift
      SessionStateTests.swift
```

Note on the test files: the project file lists exactly three test files.
The WAV wrap tests (`WAVWrapTests`) live as a second test class inside
`WireProtocolTests.swift` so the project file stays accurate. Do not add
a fourth test file without also updating the project.

## Architecture in one pass

- `CaptureSource` is the protocol every capture path implements.
  `GlassesSource` is the only file that imports the toolkit SDK, and it
  owns `DATRegistration`. `PhoneSource` uses the phone mic and camera.
  `MockSource` feeds synthetic frames so the rest of the app can be
  exercised with no hardware.
- `SessionManager` owns the active source. The app launches in PHONE mode
  by default. A watchdog watches the glasses path: no frame for over 3 s,
  a hinge close event, or 3 jitter strikes all mean the glasses path is
  unhealthy.
- There is exactly ONE failover path, `failoverToPhone`, triggered by the
  watchdog or by the GO PHONE-ONLY kill switch in the UI. After a
  failover there is a 60 s cooldown before the glasses can re-attach.
  The session ID survives failover, so the server sees one continuous
  session.
- `PTTController` implements push-to-talk on top of the session state
  machine (`Common/SessionState.swift`). Talk start drains the 10 s
  pre-roll buffer first, then streams live frames.
- `OfflineStaging` is a 500 MB disk ring. When the WebSocket is down,
  frames are written to the ring instead of being dropped. An eviction
  counter tracks how many frames the ring had to drop, and the count is
  reported, never hidden. See PROTOCOL.md for replay order.
- Audio is PCM16, 16 kHz, mono, in 100 ms frames (3200 bytes). Photos
  are JPEG, 960 px long edge, quality 0.7, one every 2.5 s while a
  session is live, plus one eager photo at talk start.
- The phone mic is the deterministic audio path. The glasses mic (the
  camera-stream audio track) is experimental in toolkit 1.0 and is not
  on the demo-critical path.

## Building on the Mac

You need a Mac with Xcode. The app targets iOS 17 or later.

1. Pair the glasses to this phone first, in the Meta AI app. The bridge
   cannot pair glasses by itself. If the glasses belong to someone else,
   their phone and their Meta account are involved in this step, so do
   it on the setup day, not on event day.
2. Open `InsightBridge/InsightBridge.xcodeproj` in Xcode.
3. Set your signing team: select the project, then the InsightBridge
   target, and set `DEVELOPMENT_TEAM` (Signing and Capabilities, or in
   `Config.xcconfig` if your checkout has one).
4. Set the Meta client token through the build, never in source. Put
   `META_CLIENT_TOKEN` in `Config.xcconfig` or as an environment
   variable on the scheme. The token is read from build settings at
   runtime. It must never be hardcoded or committed.
5. Dev mode: `MetaAppID` is `0` in Info.plist. Zero means developer mode,
   which skips production app registration with Meta. Keep it at 0 for
   the hackathon build.
6. Build and run on the physical iPhone. The glasses paths do not work
   in the simulator; use `MockSource` there.
7. Run the tests with Cmd-U. They cover the wire header, the pre-roll
   buffer, the session state machine, and the WAV wrapper. They need no
   hardware and no network.

## Toolkit version decision

- This project targets Meta Wearables DAT toolkit 1.0, pinned at
  1.0.1 or later (`>= 1.0.1`).
- Do not use 1.0.0: it has a camera freeze bug that hangs the photo
  stream, which is fatal for this app.
- Do not copy the pin from Findr: Findr pinned DAT 0.7.0. That pin is
  Findr's, for Findr's API surface. This project was designed against
  the 1.0 API and the 0.7 pin will not build it.
- Dev mode credentials: `MetaAppID = 0` in Info.plist, and the client
  token arrives through build variables only (see the build steps
  above). No credential lives in the repo.

## Transport decision

- Glasses to iPhone uses Bluetooth Classic. The choice is frozen in
  Info.plist and entitlements. There is no runtime switch and there
  will not be one.
- Why: the phone has to stay on the laptop-hosted Wi-Fi network
  (SSID `insight-net`, laptop at 192.168.2.1) to reach the server. The
  toolkit Wi-Fi transport would move the phone onto the glasses access
  point and strand the server link. Bluetooth Classic also starts a
  stream session in about a second, versus roughly 10 s on Wi-Fi, and
  its bandwidth is plenty for 0.4 fps JPEGs plus 32 KB/s audio.
- iPhone to laptop is a WebSocket on that Wi-Fi network. Full details
  in PROTOCOL.md.

## Honest caveat: nothing here was compiled on the build machine

This code was written on a Linux VM. Swift and Xcode do not run there,
so none of it has been compiled, run, or tested on the machine that
produced it. The code is written carefully against the spec and against
PROTOCOL.md, and the unit tests encode the expected behavior, but the
first Mac build will very likely surface small errors: a missed import,
a slightly wrong API name in the toolkit surface, a type mismatch.
That is expected. Budget time on the setup day to fix the first-build
errors in Xcode before touching any feature work, and run the unit
tests there first, because they pin the wire format the server expects.

## Setup-day checklist (short version)

Full checklist lives with the project docs. The bridge-specific gates:

1. isDevMode log: launch the app and confirm the log line shows the
   toolkit in dev mode (MetaAppID 0) before pairing anything.
2. Audio format assert: capture from both the phone mic and the
   glasses mic and confirm the assert passes for PCM16, 16 kHz, mono
   on both, and that the server accepts both.
3. Photo latency: measure the real time from talk start to the eager
   photo landing on the server. Write the number down; the demo plan
   depends on it.
4. Kill-switch drill: mid-session, hit GO PHONE-ONLY and confirm the
   session fails over to the phone path with the same session ID, no
   restart, no lost frames. Then run the watchdog drill (cover or
   close the glasses) and confirm the same single failover path fires.

## More

- Wire protocol: [PROTOCOL.md](PROTOCOL.md)
- Server and overall project status: `../BUILD-STATUS.md` and
  `../README.md` at the repo root.
