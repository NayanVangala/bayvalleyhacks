import XCTest
@testable import InsightBridge

/// Tests for Session/PreRollBuffer.swift.
///
/// The buffer holds PCM16 16 kHz mono audio: 32,000 bytes per second.
/// Default capacity is 320,000 bytes, which is 10 s of audio. The
/// push-to-talk controller drains it at talk start so the start of a
/// sentence is never clipped.
final class PreRollBufferTests: XCTestCase {

    private func makePattern(byteCount: Int) -> Data {
        Data((0..<byteCount).map { UInt8($0 % 251) })
    }

    func testAppendAndDrainReturnsBytesInOrderAndClears() {
        let buffer = PreRollBuffer(capacityBytes: 1024)
        buffer.append(Data([1, 2, 3]))
        buffer.append(Data([4, 5]))
        buffer.append(Data([6, 7, 8, 9]))

        XCTAssertEqual(buffer.bufferedBytes, 9)
        let drained = buffer.drain()
        XCTAssertEqual(drained, Data([1, 2, 3, 4, 5, 6, 7, 8, 9]))

        XCTAssertEqual(buffer.bufferedBytes, 0)
        XCTAssertEqual(buffer.durationSeconds, 0, accuracy: 0.0001)
        XCTAssertTrue(buffer.drain().isEmpty)
    }

    func testDrainOnEmptyReturnsEmpty() {
        let buffer = PreRollBuffer(capacityBytes: 1024)
        XCTAssertTrue(buffer.drain().isEmpty)
        XCTAssertEqual(buffer.bufferedBytes, 0)
    }

    func testAppendAfterDrainStillWorks() {
        let buffer = PreRollBuffer(capacityBytes: 1024)
        buffer.append(Data([10, 20]))
        _ = buffer.drain()
        buffer.append(Data([30]))
        XCTAssertEqual(buffer.drain(), Data([30]))
    }

    func testOverflowKeepsOnlyNewestCapacityBytes() {
        let capacity = 1024
        let overflow = 1000
        let full = makePattern(byteCount: capacity + overflow)
        let buffer = PreRollBuffer(capacityBytes: capacity)

        // Append the whole stream across uneven chunks so the test does
        // not depend on chunk boundaries.
        buffer.append(full.prefix(700))
        buffer.append(full.dropFirst(700).prefix(700))
        buffer.append(full.suffix(full.count - 1400))

        XCTAssertEqual(buffer.bufferedBytes, capacity)

        let drained = buffer.drain()
        XCTAssertEqual(drained.count, capacity)

        // The drained bytes must be the tail of the appended stream:
        // the first drained byte is the byte at offset `overflow` in
        // the full stream.
        let expected = [UInt8](full)
        let got = [UInt8](drained)
        XCTAssertEqual(got[0], expected[overflow])
        XCTAssertEqual(Array(got[0..<16]), Array(expected[overflow..<(overflow + 16)]))
        XCTAssertEqual(drained, Data(full.suffix(capacity)))
    }

    func testSingleOversizedAppendIsTrimmedToCapacity() {
        let capacity = 512
        let full = makePattern(byteCount: capacity * 3)
        let buffer = PreRollBuffer(capacityBytes: capacity)
        buffer.append(full)
        XCTAssertEqual(buffer.bufferedBytes, capacity)
        XCTAssertEqual(buffer.drain(), Data(full.suffix(capacity)))
    }

    func testDurationSecondsMath() {
        // 160,000 bytes at 32,000 bytes per second is exactly 5 s.
        let buffer = PreRollBuffer(capacityBytes: 320_000)
        buffer.append(makePattern(byteCount: 160_000))
        XCTAssertEqual(buffer.bufferedBytes, 160_000)
        XCTAssertEqual(buffer.durationSeconds, 5.0, accuracy: 0.0001)
    }

    func testDefaultCapacityHoldsTenSeconds() {
        let buffer = PreRollBuffer(capacityBytes: 320_000)
        buffer.append(makePattern(byteCount: 320_000))
        XCTAssertEqual(buffer.bufferedBytes, 320_000)
        XCTAssertEqual(buffer.durationSeconds, 10.0, accuracy: 0.0001)

        // One more byte pushes the oldest byte out, duration stays 10 s.
        buffer.append(Data([0xFF]))
        XCTAssertEqual(buffer.bufferedBytes, 320_000)
        XCTAssertEqual(buffer.durationSeconds, 10.0, accuracy: 0.0001)
    }

    func testClearResetsBuffer() {
        let buffer = PreRollBuffer(capacityBytes: 1024)
        buffer.append(makePattern(byteCount: 500))
        XCTAssertEqual(buffer.bufferedBytes, 500)

        buffer.clear()
        XCTAssertEqual(buffer.bufferedBytes, 0)
        XCTAssertEqual(buffer.durationSeconds, 0, accuracy: 0.0001)
        XCTAssertTrue(buffer.drain().isEmpty)
    }
}
