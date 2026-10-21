// Config.swift
// InsightBridge
//
// Reads server settings from Info.plist and holds the watchdog and
// failover constants in one place. No secrets live here: the Meta
// ClientToken comes from build variables via Info.plist, never from
// a literal in this file.

import Foundation

enum Config {
    // MARK: - Server (from Info.plist, with safe defaults)

    /// Value of OnRecordServerHost in Info.plist.
    /// That value comes from SERVER_HOST in Config.xcconfig.
    /// Falls back to the default laptop address on the demo network.
    static var serverHost: String {
        if let value = Bundle.main.object(forInfoDictionaryKey: "OnRecordServerHost") as? String,
           !value.isEmpty,
           !value.hasPrefix("$(") {
            return value
        }
        return "192.168.2.1"
    }

    /// Value of OnRecordServerPort in Info.plist, or 8000.
    static var serverPort: Int {
        if let value = Bundle.main.object(forInfoDictionaryKey: "OnRecordServerPort") as? Int,
           value > 0 {
            return value
        }
        if let value = Bundle.main.object(forInfoDictionaryKey: "OnRecordServerPort") as? String,
           let port = Int(value),
           port > 0 {
            return port
        }
        return 8000
    }

    /// Base HTTP URL for the laptop server, for example http://192.168.2.1:8000
    static var httpBaseURL: URL {
        // The host comes from build settings today, but it is one
        // settings field away from being user editable, and a host
        // containing spaces or slashes makes URL(string:) return nil.
        // Fall back to the default server instead of crashing.
        URL(string: "http://\(serverHost):\(serverPort)")
            ?? URL(string: "http://192.168.2.1:8000")!
    }

    /// WebSocket ingest URL for one session.
    /// Format: ws://host:port/ingest/audio?session_id=<uuid>
    static func wsURL(sessionID: UUID) -> URL {
        URL(string: "ws://\(serverHost):\(serverPort)/ingest/audio?session_id=\(sessionID.uuidString)")
            ?? URL(string: "ws://192.168.2.1:8000/ingest/audio?session_id=\(sessionID.uuidString)")!
    }

    /// Enrollment endpoint. The enrollment flow posts one person (JSON:
    /// {name, photo_b64, voice_b64}) to this URL.
    /// XCODE-VERIFY: confirm with the server team that enrollment lives
    /// at POST /api/enroll (alias /api/people/enroll) and not at
    /// the old /enroll multipart path.
    static var enrollmentURL: URL {
        httpBaseURL.appendingPathComponent("api/enroll")
    }

    // MARK: - Watchdog and failover

    /// Seconds with no video frames before the watchdog treats the
    /// glasses source as dead.
    static let noFrameTimeoutSeconds: TimeInterval = 3.0

    /// Seconds to wait for the FIRST glasses video frame during attach.
    /// This must clear DAT's own ~30s SoftAP link-establishment timeout:
    /// a real first arm takes ~14s end to end (waitingForDevice, then
    /// streaming), so the old noFrameTimeoutSeconds + 2 budget always
    /// failed over to phone before the camera transport ever came up.
    /// See docs/DAT-WIFI-ENTITLEMENTS.md.
    static let initialAttachTimeoutSeconds: TimeInterval = 40.0

    /// Window in which jitter strikes are counted.
    static let jitterStrikeWindowSeconds: TimeInterval = 30

    /// Jitter strikes inside the window that trigger a failover.
    static let jitterStrikeLimit: Int = 3

    /// Minimum seconds between automatic failovers.
    static let failoverCooldownSeconds: TimeInterval = 60

    /// How many times to try reattaching to the glasses automatically
    /// before staying on the fallback source.
    static let maxAutoReattachAttempts: Int = 3

    // MARK: - Capture cadence

    /// Seconds between still photos during a recording segment.
    static let photoCadenceSeconds: TimeInterval = 2.5

    /// Seconds of audio kept in the pre-roll buffer. When talk starts,
    /// the buffered audio is sent first so the first words are not lost.
    static let preRollSeconds: TimeInterval = 10
}
