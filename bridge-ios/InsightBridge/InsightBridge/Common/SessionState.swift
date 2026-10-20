// SessionState.swift
// InsightBridge
//
// Pure Swift mirror of the server session state machine.
// No SDK, no networking, no UI. This file is unit tested directly,
// so keep the public API exactly as written here.

import Foundation

/// Conversation lifecycle states. Raw values match the server.
enum ConversationState: String, Codable {
    case idle
    case armed
    case recording
    case paused
    case ended
}

/// Enforces legal conversation transitions.
///
/// Legal transitions:
/// - idle to armed, via arm(sessionID:)
/// - armed to recording, via beginTalk()
/// - recording to armed, via endTalk()
/// - armed to paused, via pause()
/// - recording to paused, via pause()
/// - paused to armed, via resume()
/// - any state to ended, via end()
///
/// Every other transition is illegal: the method returns false and
/// leaves the machine unchanged.
struct SessionStateMachine {
    private(set) var state: ConversationState
    private(set) var sessionID: UUID?
    private(set) var segmentIndex: UInt32

    init() {
        self.state = .idle
        self.sessionID = nil
        self.segmentIndex = 0
    }

    @discardableResult
    mutating func arm(sessionID: UUID) -> Bool {
        guard state == .idle else { return false }
        self.sessionID = sessionID
        self.segmentIndex = 0
        self.state = .armed
        return true
    }

    @discardableResult
    mutating func beginTalk() -> Bool {
        guard state == .armed else { return false }
        // segmentIndex is NOT incremented here: it names the segment
        // currently open, and PROTOCOL.md starts the first segment at
        // index 0. The count of completed segments advances in
        // endTalk(), when a segment actually completes.
        state = .recording
        return true
    }

    @discardableResult
    mutating func endTalk() -> Bool {
        guard state == .recording else { return false }
        segmentIndex += 1
        state = .armed
        return true
    }

    @discardableResult
    mutating func pause() -> Bool {
        guard state == .armed || state == .recording else { return false }
        state = .paused
        return true
    }

    @discardableResult
    mutating func resume() -> Bool {
        guard state == .paused else { return false }
        state = .armed
        return true
    }

    @discardableResult
    mutating func end() -> Bool {
        // Any state may end, including idle and ended itself.
        state = .ended
        return true
    }
}
