import XCTest
@testable import InsightBridge

/// Tests for Net/WireProtocol.swift: BinaryFrameHeader, ControlMessage,
/// and ServerMessage. The wire format is fixed by PROTOCOL.md, so these
/// tests assert exact bytes, not just round trips.
final class WireProtocolTests: XCTestCase {

    // MARK: - BinaryFrameHeader round trip

    func testHeaderRoundTripAudio() {
        let header = BinaryFrameHeader(
            kind: .audio,
            flags: 0x01,
            segIdx: 7,
            frameSeq: 42,
            captureTsMs: 12_340
        )
        let encoded = header.encode()
        XCTAssertEqual(encoded.count, 16)
        let decoded = BinaryFrameHeader.decode(encoded)
        XCTAssertNotNil(decoded)
        XCTAssertEqual(decoded, header)
    }

    func testHeaderRoundTripPhoto() {
        let header = BinaryFrameHeader(
            kind: .photo,
            flags: 0x08,
            segIdx: 0,
            frameSeq: 1_000_000,
            captureTsMs: 65_000
        )
        let encoded = header.encode()
        XCTAssertEqual(encoded.count, 16)
        let decoded = BinaryFrameHeader.decode(encoded)
        XCTAssertNotNil(decoded)
        XCTAssertEqual(decoded, header)
        XCTAssertEqual(decoded?.kind, .photo)
    }

    func testHeaderRoundTripZeroAndMaxValues() {
        let zero = BinaryFrameHeader(
            kind: .audio, flags: 0, segIdx: 0, frameSeq: 0, captureTsMs: 0
        )
        XCTAssertEqual(BinaryFrameHeader.decode(zero.encode()), zero)

        let maxed = BinaryFrameHeader(
            kind: .photo,
            flags: UInt8.max,
            segIdx: UInt32.max,
            frameSeq: UInt32.max,
            captureTsMs: UInt32.max
        )
        XCTAssertEqual(BinaryFrameHeader.decode(maxed.encode()), maxed)
    }

    // MARK: - Header byte layout

    func testHeaderMagicAndKindBytes() {
        let audio = BinaryFrameHeader(
            kind: .audio, flags: 0, segIdx: 0, frameSeq: 0, captureTsMs: 0
        )
        let audioBytes = [UInt8](audio.encode())
        XCTAssertEqual(audioBytes.count, 16)
        XCTAssertEqual(audioBytes[0], 0x4F)
        XCTAssertEqual(audioBytes[1], 0x52)
        XCTAssertEqual(audioBytes[2], 1)

        let photo = BinaryFrameHeader(
            kind: .photo, flags: 0, segIdx: 0, frameSeq: 0, captureTsMs: 0
        )
        let photoBytes = [UInt8](photo.encode())
        XCTAssertEqual(photoBytes[0], 0x4F)
        XCTAssertEqual(photoBytes[1], 0x52)
        XCTAssertEqual(photoBytes[2], 2)
    }

    func testHeaderEndianness() {
        // All multi byte fields are big endian. segIdx 0x01020304 must
        // land as the byte sequence 01 02 03 04 at offset 4.
        let header = BinaryFrameHeader(
            kind: .audio,
            flags: 0,
            segIdx: 0x0102_0304,
            frameSeq: 0x0506_0708,
            captureTsMs: 0x090A_0B0C
        )
        let bytes = [UInt8](header.encode())
        XCTAssertEqual(Array(bytes[4..<8]), [0x01, 0x02, 0x03, 0x04])
        XCTAssertEqual(Array(bytes[8..<12]), [0x05, 0x06, 0x07, 0x08])
        XCTAssertEqual(Array(bytes[12..<16]), [0x09, 0x0A, 0x0B, 0x0C])
    }

    func testHeaderFlagsPreserved() {
        let header = BinaryFrameHeader(
            kind: .audio,
            flags: 0xA5,
            segIdx: 3,
            frameSeq: 9,
            captureTsMs: 100
        )
        let bytes = [UInt8](header.encode())
        XCTAssertEqual(bytes[3], 0xA5)
        let decoded = BinaryFrameHeader.decode(header.encode())
        XCTAssertEqual(decoded?.flags, 0xA5)
    }

    // MARK: - Header validation

    func testDecodeRejectsWrongMagic() {
        let header = BinaryFrameHeader(
            kind: .audio, flags: 0, segIdx: 1, frameSeq: 2, captureTsMs: 3
        )
        var bytes = [UInt8](header.encode())

        bytes[0] = 0x00
        XCTAssertNil(BinaryFrameHeader.decode(Data(bytes)))

        bytes = [UInt8](header.encode())
        bytes[1] = 0x00
        XCTAssertNil(BinaryFrameHeader.decode(Data(bytes)))

        bytes = [UInt8](header.encode())
        bytes[0] = 0x52
        bytes[1] = 0x4F
        XCTAssertNil(BinaryFrameHeader.decode(Data(bytes)))
    }

    func testDecodeRejectsTruncatedData() {
        let header = BinaryFrameHeader(
            kind: .photo, flags: 0, segIdx: 1, frameSeq: 2, captureTsMs: 3
        )
        let encoded = header.encode()
        XCTAssertNil(BinaryFrameHeader.decode(Data()))
        XCTAssertNil(BinaryFrameHeader.decode(encoded.prefix(1)))
        XCTAssertNil(BinaryFrameHeader.decode(encoded.prefix(8)))
        XCTAssertNil(BinaryFrameHeader.decode(encoded.prefix(15)))
    }

    func testDecodeRejectsUnknownKind() {
        // A frame with valid magic but a kind byte that is not 1 or 2 is
        // not a frame this protocol understands.
        var bytes = [UInt8](
            BinaryFrameHeader(
                kind: .audio, flags: 0, segIdx: 0, frameSeq: 0, captureTsMs: 0
            ).encode()
        )
        bytes[2] = 99
        XCTAssertNil(BinaryFrameHeader.decode(Data(bytes)))
    }

    // MARK: - ControlMessage

    func testTalkStartEncodesSnakeCaseKeys() throws {
        let message = ControlMessage.talkStart(sessionID: UUID(), segIdx: 3, t0MonoMs: 4200)
        let data = try JSONEncoder().encode(message)
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertTrue(json.contains("seg_idx"), json)
        XCTAssertTrue(json.contains("t0_mono_ms"), json)
        XCTAssertTrue(json.contains("talk_start"), json)
    }

    func testControlMessageDecodesBack() throws {
        let message = ControlMessage.talkStart(sessionID: UUID(), segIdx: 11, t0MonoMs: 987_654)
        let data = try JSONEncoder().encode(message)
        let decoded = try JSONDecoder().decode(ControlMessage.self, from: data)
        // Re-encoding the decoded value must reproduce the same fields.
        let reEncoded = try JSONEncoder().encode(decoded)
        let json = String(decoding: reEncoded, as: UTF8.self)
        XCTAssertTrue(json.contains("seg_idx"), json)
        XCTAssertTrue(json.contains("11"), json)
        XCTAssertTrue(json.contains("987654"), json)
    }

    // MARK: - ServerMessage

    // ServerMessage.parse takes a String and never returns nil:
    // anything unrecognized parses to the .unknown case.

    func testParseCaptureNow() {
        let photoID = UUID().uuidString
        let json = """
        {"type":"capture_now","reason":"operator_request","photo_id":"\(photoID)"}
        """
        let message = ServerMessage.parse(json)
        XCTAssertTrue(
            String(describing: message).contains("captureNow"),
            "expected captureNow, got \(message)"
        )
    }

    func testParseError() {
        let json = """
        {"type":"error","code":"UNKNOWN_SESSION","message":"no such session","fatal":false}
        """
        let message = ServerMessage.parse(json)
        XCTAssertTrue(
            String(describing: message).contains("error"),
            "expected error, got \(message)"
        )
    }

    func testParseWelcome() {
        let sessionID = UUID().uuidString
        let json = """
        {"type":"welcome","session_id":"\(sessionID)","server_time":"2026-10-25T18:02:11.050Z","ok":true}
        """
        let message = ServerMessage.parse(json)
        XCTAssertTrue(
            String(describing: message).contains("welcome"),
            "expected welcome, got \(message)"
        )
    }

    func testParsePong() {
        let json = """
        {"type":"pong","t0_mono_ms":4200,"t_mono_ms":4200,"server_time":"2026-10-25T18:02:21.050Z"}
        """
        let message = ServerMessage.parse(json)
        XCTAssertTrue(
            String(describing: message).contains("pong"),
            "expected pong, got \(message)"
        )
    }

    func testParseGarbageBecomesUnknown() {
        for raw in ["not json", "", "{}"] {
            let message = ServerMessage.parse(raw)
            XCTAssertTrue(
                String(describing: message).contains("unknown"),
                "expected unknown for \(raw.debugDescription), got \(message)"
            )
        }
    }
}

/// Tests for Enrollment/EnrollmentAPI.wavWrapPCM.
///
/// This class lives in WireProtocolTests.swift on purpose: the Xcode
/// project file lists exactly three test files (WireProtocolTests,
/// PreRollBufferTests, SessionStateTests), so the WAV tests ride along
/// in this file instead of adding a fourth file the project does not
/// reference.
final class WAVWrapTests: XCTestCase {

    // MARK: - Helpers

    private func le16(_ data: Data, at offset: Int) -> UInt16 {
        let bytes = [UInt8](data)
        return UInt16(bytes[offset]) | (UInt16(bytes[offset + 1]) << 8)
    }

    private func le32(_ data: Data, at offset: Int) -> UInt32 {
        let bytes = [UInt8](data)
        return UInt32(bytes[offset])
            | (UInt32(bytes[offset + 1]) << 8)
            | (UInt32(bytes[offset + 2]) << 16)
            | (UInt32(bytes[offset + 3]) << 24)
    }

    private func ascii(_ data: Data, in range: Range<Int>) -> String {
        String(decoding: data[range], as: UTF8.self)
    }

    private func makePCM(byteCount: Int) -> Data {
        Data((0..<byteCount).map { UInt8($0 % 251) })
    }

    // MARK: - Tests

    func testLengthIsHeaderPlusPCM() {
        // 3200 bytes is one 100 ms frame of PCM16 16 kHz mono.
        let pcm = makePCM(byteCount: 3200)
        let wav = EnrollmentAPI.wavWrapPCM(pcm, sampleRate: 16000)
        XCTAssertEqual(wav.count, 44 + pcm.count)
    }

    func testRIFFAndWAVEMagic() {
        let wav = EnrollmentAPI.wavWrapPCM(makePCM(byteCount: 100), sampleRate: 16000)
        XCTAssertEqual(ascii(wav, in: 0..<4), "RIFF")
        XCTAssertEqual(ascii(wav, in: 8..<12), "WAVE")
        XCTAssertEqual(ascii(wav, in: 12..<16), "fmt ")
        XCTAssertEqual(ascii(wav, in: 36..<40), "data")
    }

    func testByteRateIsSampleRateTimesTwo() {
        // PCM16 mono: 2 bytes per sample, one channel.
        let wav16k = EnrollmentAPI.wavWrapPCM(makePCM(byteCount: 100), sampleRate: 16000)
        XCTAssertEqual(le32(wav16k, at: 28), 32_000)
        XCTAssertEqual(le32(wav16k, at: 24), 16_000)

        let wav8k = EnrollmentAPI.wavWrapPCM(makePCM(byteCount: 100), sampleRate: 8000)
        XCTAssertEqual(le32(wav8k, at: 28), 16_000)
        XCTAssertEqual(le32(wav8k, at: 24), 8000)
    }

    func testDataChunkSizeMatchesPCM() {
        let pcm = makePCM(byteCount: 1001)
        let wav = EnrollmentAPI.wavWrapPCM(pcm, sampleRate: 16000)
        XCTAssertEqual(le32(wav, at: 40), UInt32(pcm.count))
        // RIFF chunk size is 36 plus the data size.
        XCTAssertEqual(le32(wav, at: 4), UInt32(36 + pcm.count))
    }

    func testCanonicalFormatFields() {
        let wav = EnrollmentAPI.wavWrapPCM(makePCM(byteCount: 64), sampleRate: 16000)
        XCTAssertEqual(le32(wav, at: 16), 16)   // fmt chunk size
        XCTAssertEqual(le16(wav, at: 20), 1)    // audio format: PCM
        XCTAssertEqual(le16(wav, at: 22), 1)    // channels: mono
        XCTAssertEqual(le16(wav, at: 32), 2)    // block align: 2 bytes
        XCTAssertEqual(le16(wav, at: 34), 16)   // bits per sample
    }

    func testPayloadIsOriginalPCM() {
        let pcm = makePCM(byteCount: 500)
        let wav = EnrollmentAPI.wavWrapPCM(pcm, sampleRate: 16000)
        XCTAssertEqual(Data(wav.suffix(pcm.count)), pcm)
    }

    func testEmptyPCMProducesBareHeader() {
        let wav = EnrollmentAPI.wavWrapPCM(Data(), sampleRate: 16000)
        XCTAssertEqual(wav.count, 44)
        XCTAssertEqual(le32(wav, at: 40), 0)
        XCTAssertEqual(ascii(wav, in: 0..<4), "RIFF")
    }
}
