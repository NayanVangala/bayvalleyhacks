// MediaTypes.swift
// InsightBridge
//
// Shared media value types used across capture, session, and networking.
// This file is SDK free on purpose: it imports only Foundation and
// CoreVideo so every layer, including tests, can use these types without
// pulling in the Meta Wearables DAT SDK.

import Foundation
import CoreVideo

/// Where a piece of media actually came from.
enum CaptureOrigin: String, Codable {
    case glasses
    case phone
    case mock
}

/// Fixed audio and photo format constants for the whole pipeline.
///
/// Audio is PCM16 mono at 16000 Hz. One frame is 100 ms, which is
/// 1600 samples, which is 3200 bytes. The server expects exactly this.
enum AudioFormat {
    static let sampleRate: Int = 16000
    static let frameMs: Int = 100
    static let frameBytes: Int = 3200
    static let photoLongEdgePx: Int = 960
    static let photoJPEGQuality: Double = 0.7
}

/// One chunk of PCM16 mono audio.
struct AudioChunk {
    /// Monotonic sequence number within the active capture source.
    var seq: UInt64
    /// PCM16 mono samples at `sampleRate`. Normally `AudioFormat.frameBytes` long.
    var pcm: Data
    var sampleRate: Int = AudioFormat.sampleRate
    var timestamp: Date
    var origin: CaptureOrigin
}

/// One video frame from a capture source.
///
/// Frames are used for liveness, hinge and watchdog checks, and as the
/// source for still photos. They are not streamed to the server as video.
struct VideoFrame {
    var pixelBuffer: CVPixelBuffer
    var timestamp: Date
    var origin: CaptureOrigin
}

/// A point in time health snapshot for a capture source.
struct SourceHealth {
    var origin: CaptureOrigin
    var alive: Bool
    /// Battery level in 0...1, or nil when the source cannot report it.
    var batteryLevel: Double?
    /// True when the glasses hinge is closed, when that is known.
    /// Phone and mock sources report false.
    var hingeClosed: Bool
    /// Seconds since the last frame arrived from this source.
    var lastFrameAge: TimeInterval
    /// Short human readable detail for the status UI. No secrets in here.
    var detail: String
}

/// Why the session manager failed over, or considered failing over,
/// from one capture source to another.
enum FailoverReason: String {
    case watchdogNoFrames
    case sessionError
    case hingeClosed
    case jitter
    case manual
    case glassesUnavailable
}

/// One still photo, JPEG encoded.
struct StillPhoto {
    var jpeg: Data
    var timestamp: Date
    var origin: CaptureOrigin
    /// Eager photos are taken right at talk start so the server has a
    /// face to work with early. Non eager photos follow the normal cadence.
    var eager: Bool
}
