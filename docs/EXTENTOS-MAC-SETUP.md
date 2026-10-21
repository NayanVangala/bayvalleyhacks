# Extentos Mac setup (Xcode, Nayan runs this)

Date 2026-10-21. Branch: feature/extentos-bridge. This is the Xcode-side
integration checklist for the Extentos bridge replacement. Run every step on
the Mac; the code side of this branch (chunks A, B, C) is not done until it
compiles and runs here.

Floors: iOS 17.0 deployment target, Swift 6 toolchain, Xcode 16.0 or later.

## 1. Add the SPM package

1. Xcode, File, Add Package Dependencies.
2. Paste `https://github.com/extentos/swift-glasses`. Version rule: Up to Next
   Major starting at 2.8.0 (rule floor 2.8.0, not 2.0.0).
3. Link products to the app target:
   - GlassesCore: always (app target).
   - GlassesUI: yes, the ExtentosConnectionPage pairing UI is the canonical
     registration view.
   - GlassesTesting: test target only (FakeExtentosGlasses for unit tests).
   - Do NOT link GlassesLocal or GlassesLocalVoice (they pull MLX and we do
     not need them).
4. Confirm Meta's DAT iOS repo (facebook/meta-wearables-dat-ios, 0.8.0 to
   0.9.0) resolved transitively with no auth or token. Nothing to do if it
   did; flag it if Xcode asks for credentials.

## 2. Mint the Extentos keys

1. Run the Extentos MCP server `generateConnectionModule` tool once for this
   project (Node 20+ needed: `claude mcp add extentos -- npx -y
   @extentos/mcp-server@latest` if the server is not already added).
2. Confirm `EXTENTOSProjectKey` is minted into the build and stored in the
   gitignored xcconfig, NOT committed.
3. Confirm `EXTENTOS_APP_ID` (reverse-DNS project identity, decoupled from the
   bundle ID) is in Info.plist; ExtentosConfig(appId:) reads it at bootstrap.
4. If real hardware fails at runtime with `AssistantError.NoApiKey`, the
   project key did not reach the app: go back to this step, the simulator
   works without it so do not trust a simulator pass.

## 3. Fill the MWDAT dict from the Meta Developer Center

Info.plist `MWDAT` dict keys:

- `MetaAppID` (required; Meta App ID from the Wearables developer console)
- `ClientToken` (required for the camera/display path)
- `TeamID` (required; your Apple Team ID)
- `AppLinkURLScheme` (required; the custom URL scheme the Meta AI app calls
  back into after registration)
- `DAMEnabled`: set explicitly per the Extentos integration notes.

Meta Developer Center setup: developer account and org exist, project created
with the DAT product enabled, iOS bundle ID plus Team ID registered. Note:
the console may have an age gate (16+); fallback is a parent's account.

Also confirm these Info.plist keys already present or added:

- `CFBundleURLTypes` matching `AppLinkURLScheme`
- `LSApplicationQueriesSchemes`: `fb-viewapp`
- `UISupportedExternalAccessoryProtocols`: `com.meta.ar.wearable`
- `UIBackgroundModes`: bluetooth-peripheral, external-accessory
- `NSBluetoothAlwaysUsageDescription`, `NSCameraUsageDescription`,
  `NSMicrophoneUsageDescription`, `NSSpeechRecognitionUsageDescription`
- `NSLocalNetworkUsageDescription` plus a non-empty `NSBonjourServices`
  (service types from step 5)

## 4. App ID entitlements via xcodebuild

1. Confirm `InsightBridge.entitlements` carries both keys on the app target
   (Debug and Release configs via CODE_SIGN_ENTITLEMENTS):
   - `com.apple.developer.networking.wifi-info` (Access Wi-Fi Information)
   - `com.apple.developer.networking.HotspotConfiguration` (Hotspot
     Configuration)
2. Run `xcodebuild -allowProvisioningUpdates` so the App ID on the portal
   picks up both capabilities. An entitlement is granted to the app, never
   to a framework, so no SDK supplies these.
3. Verify what actually shipped, not what Xcode shows:
   `codesign -d --entitlements - /path/to/InsightBridge.app`
   Both keys must appear in the dumped entitlements.
4. Keep in mind they do not necessarily survive re-signing; re-verify after
   any manual signing change.

## 5. Fill NSBonjourServices from the DAT toolkit docs

1. `NSBonjourServices` must be non-empty or the local socket layer gates the
   camera link. The exact service types are still TBD; pull them from the DAT
   toolkit docs on the Mac and fill them in Info.plist now.
2. Also confirm the Local Network permission shows up under Settings,
   Privacy and Security, Local Network after first launch; VPNs, MDM, or
   content-filter profiles can silently block the local subnet.

## 6. Build and fix every VERIFY-ON-MAC marker

1. Build the app target, then the test target.
2. Old imports `MWDATCore`, `MWDATCamera`, `MWDATMockDevice` must be gone
   everywhere. Only `Sources/ExtentosGlassesSource.swift` and
   `Sources/ExtentosRegistration.swift` may import `GlassesCore`/`GlassesUI`.
3. Work the open VERIFY-ON-MAC list down to zero:
   - typed `DeviceId` conversion from the stored device ID string (or nil)
   - `Photo` byte path from `uri` to Data
   - `Disconnected` cause enum cases (r1 names: UserRequested,
     DeviceDroppedConnection, ThermalCritical, HingesClosed, TransportFailure)
   - full `GlassesState` case list beyond notRegistered/registered
     (r1: notRegistered, registered, deviceDiscovered, connecting, active,
     disconnected)
   - raw codec plane layout: capture one `.raw` frame, check buffer.count
     vs width x height (1.5x hints NV12/I420, 4x hints BGRA); do NOT guess a
     layout before that. Default config stays `.hvc1` until this is verified.
   - `timestampMs` epoch for audio chunks
   - `ws.glassesModel` mapping from `glasses.device.type`
   - battery: no API found in research; health carries nil, not a blocker
4. No fake import may remain in app code: tests use FakeExtentosGlasses from
   GlassesTesting; `MWDATMockDevice` is deleted.

## 7. Live handshake test vs the Python server

1. Start the Python server on the laptop (same as the old bridge days:
   POST /api/sessions, ws:// handshake, same wire protocol; the server side
   is untouched by this branch).
2. On the phone, open the app, go to ExtentosConnectionPage, register via
   the Meta AI deep-link once, and confirm the app appears in Meta AI's App
   Connections list. Connection state must reach `active`.
3. Arm a session in the app with the phone tethered to Xcode.
4. Confirm in the Xcode console:
   - hello carries the phone-generated UUID and the server answers welcome
     with the same session_id (same three fixed spots as before: hello UUID,
     JSON base64 enrollment PCM16, PREROLL flag 0x01 on the wire)
   - camera stream state transitions arming to streaming; first arm takes
     about 14 s; the iOS Wi-Fi join prompt appears on this cold launch
   - 2 fps JPEG frames arrive, 20 ms audio chunks at 16 kHz accumulate into
     the 100 ms / 3200-byte server chunks
   - temple tap pause flips the stream state to paused; hinge close maps to
     health hingeClosed and teardown; `capturePhoto` returns a 960 px,
     0.7-quality JPEG; a speaker card shows on the borrowed Display pair
     (gated on `glasses.display.isAvailable`)
5. Success means one real-model run with frames, audio, health, and a still
   all flowing through the existing session pipeline, then a second clean
   run. Both must land before the Oct 24 8 pm code freeze.

## Failure tells

- Stream sits in arming/waiting and dies at ~30 s while the link reads
  connected and HFP audio works: the Wi-Fi join never fired. See
  docs/DAT-WIFI-ENTITLEMENTS.md; check the join prompt and the shipped
  entitlements.
- `AssistantError.NoApiKey` on real hardware: project key missing; back to
  step 2.
- `connect()` called twice concurrently: connect is NOT idempotent; retries
  must go through `reconnect()`. Check the attach guards.
- Registration deep-link opens Meta AI but the app never advances past
  notRegistered: check AppLinkURLScheme plus CFBundleURLTypes and the
  `.onOpenURL` wiring to `registration.handleOpenURL`.
