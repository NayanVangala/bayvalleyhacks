// Lexia Read Mode for OnRecord. Uncompiled Swift source written Oct 2026. Bridge team: copy into the bridge-ios target and wire the marked integration points.
//
// ReadCapture.swift
// Capture abstraction and trigger handling for Read Mode.
// A capture returns JPEG data for one still image of the text in view.
// That data is passed to LexiaAPIClient.read(imageData:simplify:).

import Foundation

// MARK: - Capture errors

public enum ReadCaptureError: Error, Equatable {
    case notImplemented
    case noImageData
    case permissionDenied
}

extension ReadCaptureError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .notImplemented:
            return "This capture source is a stub and has not been wired yet."
        case .noImageData:
            return "Capture finished without image data."
        case .permissionDenied:
            return "Camera permission was not granted."
        }
    }
}

// MARK: - Capture source

/// A source that can capture one still image for Read Mode.
/// Implementations must return JPEG encoded data.
public protocol ReadCaptureSource {
    func captureStill() async throws -> Data
}

// MARK: - Stub: phone camera

/// STUB. Bridge team: wire AVFoundation here.
/// Intended implementation:
/// 1. Check AVCaptureDevice authorization for video and request it if needed.
/// 2. Configure an AVCaptureSession with a photo output.
/// 3. Capture one photo, convert it to JPEG with UIImage jpegData or
///    AVCapturePhoto fileDataRepresentation, and return the Data.
/// 4. Throw ReadCaptureError.permissionDenied when access is refused and
///    ReadCaptureError.noImageData when the capture produces no data.
public struct PhoneCameraCaptureStub: ReadCaptureSource {
    public init() {}

    public func captureStill() async throws -> Data {
        // INTEGRATION POINT: replace this throw with the AVFoundation capture described above.
        throw ReadCaptureError.notImplemented
    }
}

// MARK: - Stub: glasses camera

/// STUB. Bridge team: wire Meta Wearables DAT camera still capture here.
/// Intended implementation:
/// 1. Use the Meta Wearables Device Access Toolkit session already owned
///    by the bridge app. Do not create a second competing session here.
/// 2. Request a single still capture from the glasses camera.
/// 3. Convert the returned image to JPEG Data and return it.
/// 4. Throw ReadCaptureError.noImageData when the toolkit returns no frame.
public struct GlassesCaptureStub: ReadCaptureSource {
    public init() {}

    public func captureStill() async throws -> Data {
        // INTEGRATION POINT: wire Meta Wearables DAT camera still capture here.
        throw ReadCaptureError.notImplemented
    }
}

// MARK: - Triggers

/// Something that can signal that Read Mode should start a capture.
/// Two triggers exist in the product: a Neural Band pinch and a tap on
/// the "Read this" control on the phone.
public protocol ReadTrigger: AnyObject {
    func triggerPinch()
    func triggerTap()
}

/// Debouncing trigger handler for Read Mode.
///
/// INTEGRATION POINT: the Neural Band pinch arrives via the band input
/// handler in the bridge app. That handler should call triggerPinch()
/// on this object. The phone "Read this" button should call triggerTap().
/// Keep one shared instance so a pinch and a tap fired together do not
/// start two captures.
///
/// Debounce rule: triggers that arrive within 1.5 seconds of the last
/// accepted trigger are ignored. This covers band double fires and a
/// user tapping while a pinch is already being handled.
public final class ReadTriggerHandler: ReadTrigger {

    /// Called once per accepted pinch trigger.
    public var onPinch: (() -> Void)?

    /// Called once per accepted tap trigger.
    public var onTap: (() -> Void)?

    /// Minimum spacing between accepted triggers, in seconds.
    public let debounceInterval: TimeInterval

    private var lastAcceptedAt: Date?
    private let now: () -> Date

    public init(debounceInterval: TimeInterval = 1.5, now: @escaping () -> Date = { Date() }) {
        self.debounceInterval = debounceInterval
        self.now = now
    }

    public func triggerPinch() {
        guard acceptTrigger() else { return }
        onPinch?()
    }

    public func triggerTap() {
        guard acceptTrigger() else { return }
        onTap?()
    }

    /// Returns true and records the time when a trigger should be handled.
    /// Returns false when the trigger falls inside the debounce window.
    private func acceptTrigger() -> Bool {
        let current = now()
        if let last = lastAcceptedAt, current.timeIntervalSince(last) < debounceInterval {
            return false
        }
        lastAcceptedAt = current
        return true
    }

    /// Clears the debounce window. Useful in tests and when Read Mode is
    /// re-entered after being dismissed.
    public func resetDebounce() {
        lastAcceptedAt = nil
    }
}
