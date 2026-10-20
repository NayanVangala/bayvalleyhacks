import XCTest
@testable import InsightBridge

/// Tests for Common/SessionState.swift: SessionStateMachine.
///
/// Legal transitions:
///   idle to armed (arm)
///   armed to recording (beginTalk)
///   recording to armed (endTalk, increments segmentIndex)
///   armed to paused (pause)
///   paused to armed (resume)
///   recording to paused (pause)
///   any state to ended (end)
/// Everything else returns false and changes nothing.
final class SessionStateTests: XCTestCase {

    // MARK: - Helpers

    private func armedMachine() -> (machine: SessionStateMachine, sessionID: UUID) {
        var machine = SessionStateMachine()
        let sessionID = UUID()
        XCTAssertTrue(machine.arm(sessionID: sessionID))
        return (machine, sessionID)
    }

    private func recordingMachine() -> (machine: SessionStateMachine, sessionID: UUID) {
        var (machine, sessionID) = armedMachine()
        XCTAssertTrue(machine.beginTalk())
        return (machine, sessionID)
    }

    private func pausedMachine() -> (machine: SessionStateMachine, sessionID: UUID) {
        var (machine, sessionID) = armedMachine()
        XCTAssertTrue(machine.pause())
        return (machine, sessionID)
    }

    // MARK: - Happy path

    func testInitialStateIsIdle() {
        let machine = SessionStateMachine()
        XCTAssertEqual(machine.state, .idle)
        XCTAssertNil(machine.sessionID)
        XCTAssertEqual(machine.segmentIndex, 0)
    }

    func testHappyPath() {
        var machine = SessionStateMachine()
        let sessionID = UUID()

        XCTAssertTrue(machine.arm(sessionID: sessionID))
        XCTAssertEqual(machine.state, .armed)
        XCTAssertEqual(machine.sessionID, sessionID)
        XCTAssertEqual(machine.segmentIndex, 0)

        XCTAssertTrue(machine.beginTalk())
        XCTAssertEqual(machine.state, .recording)

        XCTAssertTrue(machine.endTalk())
        XCTAssertEqual(machine.state, .armed)
        XCTAssertEqual(machine.segmentIndex, 1)

        XCTAssertTrue(machine.pause())
        XCTAssertEqual(machine.state, .paused)

        XCTAssertTrue(machine.resume())
        XCTAssertEqual(machine.state, .armed)

        XCTAssertTrue(machine.end())
        XCTAssertEqual(machine.state, .ended)
    }

    func testSegmentIndexCountsCompletedSegments() {
        var (machine, _) = armedMachine()
        for expected in 1...3 {
            XCTAssertTrue(machine.beginTalk())
            XCTAssertTrue(machine.endTalk())
            XCTAssertEqual(machine.segmentIndex, UInt32(expected))
            XCTAssertEqual(machine.state, .armed)
        }
    }

    func testPauseFromRecordingIsLegal() {
        var (machine, _) = recordingMachine()
        XCTAssertTrue(machine.pause())
        XCTAssertEqual(machine.state, .paused)
        XCTAssertTrue(machine.resume())
        XCTAssertEqual(machine.state, .armed)
    }

    // MARK: - Illegal transitions

    func testBeginTalkFromIdleReturnsFalse() {
        var machine = SessionStateMachine()
        XCTAssertFalse(machine.beginTalk())
        XCTAssertEqual(machine.state, .idle)
        XCTAssertNil(machine.sessionID)
        XCTAssertEqual(machine.segmentIndex, 0)
    }

    func testArmTwiceReturnsFalseAndKeepsFirstSession() {
        var (machine, firstID) = armedMachine()
        let secondID = UUID()
        XCTAssertFalse(machine.arm(sessionID: secondID))
        XCTAssertEqual(machine.state, .armed)
        XCTAssertEqual(machine.sessionID, firstID)
    }

    func testResumeFromRecordingReturnsFalse() {
        var (machine, _) = recordingMachine()
        XCTAssertFalse(machine.resume())
        XCTAssertEqual(machine.state, .recording)
    }

    func testEndTalkFromArmedReturnsFalseAndDoesNotIncrement() {
        var (machine, _) = armedMachine()
        XCTAssertFalse(machine.endTalk())
        XCTAssertEqual(machine.state, .armed)
        XCTAssertEqual(machine.segmentIndex, 0)
    }

    func testOtherIllegalTransitionsChangeNothing() {
        var idle = SessionStateMachine()
        XCTAssertFalse(idle.pause())
        XCTAssertFalse(idle.resume())
        XCTAssertFalse(idle.endTalk())
        XCTAssertEqual(idle.state, .idle)

        var (armed, armedID) = armedMachine()
        XCTAssertFalse(armed.resume())
        XCTAssertEqual(armed.state, .armed)
        XCTAssertEqual(armed.sessionID, armedID)

        var (recording, _) = recordingMachine()
        XCTAssertFalse(recording.beginTalk())
        XCTAssertFalse(recording.arm(sessionID: UUID()))
        XCTAssertEqual(recording.state, .recording)

        var (paused, _) = pausedMachine()
        XCTAssertFalse(paused.beginTalk())
        XCTAssertFalse(paused.endTalk())
        XCTAssertFalse(paused.pause())
        XCTAssertEqual(paused.state, .paused)
        XCTAssertEqual(paused.segmentIndex, 0)
    }

    // MARK: - End

    func testEndIsLegalFromEveryState() {
        var idle = SessionStateMachine()
        XCTAssertTrue(idle.end())
        XCTAssertEqual(idle.state, .ended)

        var (armed, _) = armedMachine()
        XCTAssertTrue(armed.end())
        XCTAssertEqual(armed.state, .ended)

        var (recording, _) = recordingMachine()
        XCTAssertTrue(recording.end())
        XCTAssertEqual(recording.state, .ended)

        var (paused, _) = pausedMachine()
        XCTAssertTrue(paused.end())
        XCTAssertEqual(paused.state, .ended)
    }

    func testAfterEndedNothingElseIsLegal() {
        var (machine, _) = armedMachine()
        XCTAssertTrue(machine.end())
        XCTAssertEqual(machine.state, .ended)

        XCTAssertFalse(machine.arm(sessionID: UUID()))
        XCTAssertFalse(machine.beginTalk())
        XCTAssertFalse(machine.endTalk())
        XCTAssertFalse(machine.pause())
        XCTAssertFalse(machine.resume())
        XCTAssertEqual(machine.state, .ended)
    }
}
