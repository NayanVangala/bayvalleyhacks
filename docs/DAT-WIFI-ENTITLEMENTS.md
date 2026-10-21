> STATUS 2026-10-21 (feature/extentos-bridge): this branch supersedes the
> DAT-direct path with the Extentos bridge replacement. Extentos 2.8.0 bundles
> DAT iOS 0.8.0, so the SoftAP transport analysis, both entitlements, and the
> 40 s first-arm reasoning below still apply under Extentos: RealMetaTransport
> calls the same DAT stack underneath. Original DAT-direct content is preserved
> intact below the banner.

# DAT Wi-Fi / SoftAP camera transport: the 30-second tell

Source: Asger's reply on the meta-wearables-dat-ios repo (Aug 7), forwarded
by Nayan 2026-10-21. This is the diagnosis for camera streams that sit in
`waitingForDevice` and die at exactly ~30s with `StreamError.deviceNotConnected`.

## The diagnosis

Since DAT 0.8 on iOS, camera frames do NOT ride Bluetooth. The SDK first
establishes a higher-bandwidth link by having iOS join the glasses' own
SoftAP Wi-Fi network. If that join never happens, the camera transport never
establishes, and the stream sits in `waitingForDevice` until DAT's internal
link-establishment timeout closes it. A fixed timeout is exactly why the
failure lands at 30.0s every run instead of varying.

Consequences that all fit this one root cause:

- `linkState` reads `.connected` and `addLinkStateListener` stays silent,
  because "device is not connected" reports the CAMERA transport, not the
  Bluetooth link.
- HFP audio keeps working throughout: audio is on Bluetooth, frames are not.
- The readiness signal the camera transport requires (the Wi-Fi join) is not
  surfaced through `Device` anywhere.

## The fix: two entitlements on the APP target

Add both to the app target and enable them on the App ID:

- `com.apple.developer.networking.wifi-info` (Access Wi-Fi Information)
- `com.apple.developer.networking.HotspotConfiguration` (Hotspot Configuration)

`xcodebuild -allowProvisioningUpdates` handles the App ID side. An
entitlement is granted to an app and never to a framework, so no SDK can
supply these on our behalf. They also do not necessarily survive re-signing.

Verify what actually shipped, not what Xcode shows:

```
codesign -d --entitlements - /path/to/YourApp.app
```

The observable signal is a system prompt asking to join the glasses'
network. If that prompt never appears, the join is still not being
attempted.

`NSLocalNetworkUsageDescription` and `NSBonjourServices` are required as
well, but they are not a substitute: they gate the socket layer, the
entitlements gate the network join that has to succeed first.

## Confirm which layer you are stuck at (30-second test)

Open Settings, then Wi-Fi, on the phone during the 30-second window:

- It briefly switches to a glasses-named network: the join worked, the
  problem is further down, at the local network or socket layer.
- It stays put: the join never fired, which points at the entitlements.

## Warnings from real hardware runs

- On the referenced thread the entitlements were necessary but not
  sufficient: the reporter got the Join prompt and still received zero
  frames, and the thread moved on to the local network and socket layer
  (Local Network permission under Settings > Privacy & Security, plus VPNs,
  MDM, or content-filter profiles silently blocking the local subnet).
  Treat the entitlements as unblocking the first gate, not the last one.
- Do NOT stack a short arm timeout on top of DAT's ~30s timeout. A real
  first arm took 14s end to end (waitingForDevice, starting at 12s,
  streaming at 14s). A short timeout misses it and throws away DAT's own
  ~30s result, which is the most useful signal while this is still
  misconfigured. Our `Config.initialAttachTimeoutSeconds` (40s) exists for
  exactly this reason.
- The Wi-Fi prompt comes back on EVERY cold app launch, because iOS never
  saves the network. Arm the camera during the connect flow, not on the
  first shutter press, so the user pays that wait during setup. Our
  `attachGlasses()` already starts the stream at connect time.
- Version-skew theory is a dead end: a newer glasses side against older
  app-side SDK code streams fine. `compatibility()` would not catch a
  Wi-Fi entitlement gap anyway.
- The iOS integration guide still says resolution and frame rate are
  constrained by the Bluetooth Classic connection, and nothing in the setup
  docs uses the word "entitlement". That is why a thorough ruled-out list
  can still miss this.

## What we changed in this repo (2026-10-21)

1. `bridge-ios/InsightBridge/InsightBridge/InsightBridge.entitlements`:
   both entitlements, wired via `CODE_SIGN_ENTITLEMENTS` in the app
   target's Debug and Release configs.
2. `Info.plist`: `NSBonjourServices` key added (DAT's service types still
   need to be filled in from the toolkit docs on the Mac).
3. `Config.initialAttachTimeoutSeconds = 40.0`: the attach wait now clears
   DAT's ~30s link timeout instead of failing over to phone at 5s.
