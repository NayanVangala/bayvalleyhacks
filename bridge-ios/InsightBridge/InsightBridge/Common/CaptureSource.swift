// CaptureSource.swift
// InsightBridge
//
// The one protocol every capture source implements: glasses, phone,
// and mock. Session code depends only on this protocol, never on a
// concrete source or on the DAT SDK directly.
//
// Revision 2 design note:
// NO QR in this protocol. QR badges were dropped on Oct 20, 2026.
// Identity is handled server side by face and voice fusion against
// enrolled embeddings, with an Unknown fallback. Nothing in the
// capture layer reads badges, codes, or identity labels.
//
// SDK import rule:
// Zero SDK imports are allowed outside Sources/GlassesSource.swift
// and Sources/DATRegistration.swift. This file must stay SDK free.

import Foundation

protocol CaptureSource: AnyObject {
    var origin: CaptureOrigin { get }

    /// PCM16 mono audio chunks, in order, while the source is running.
    var audio: AsyncStream<AudioChunk> { get }

    /// Video frames while the source is running.
    /// Used for liveness and stills, not streamed as video.
    var video: AsyncStream<VideoFrame> { get }

    /// Health snapshots. Emitted on change and on a slow heartbeat
    /// so the watchdog can tell a quiet source from a dead one.
    var health: AsyncStream<SourceHealth> { get }

    func start() async throws
    func stop() async

    /// Capture a single still photo now.
    /// Pass eager true for the talk start photo, false for cadence photos.
    func captureStill(eager: Bool) async throws -> StillPhoto
}
