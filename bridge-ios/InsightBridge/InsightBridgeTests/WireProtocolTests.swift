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
        {"type":"pong","t0_mono_ms":4200,"server_time":"2026-10-25T18:02:21.050Z"}
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

/// Tests for Enrollment/EnrollmentAPI's Revision 3 JSON contract:
/// POST /api/enroll with {name, photo_b64, voice_b64}, voice as raw
/// PCM16 little endian 16 kHz mono (no WAV header).
///
/// This class lives in WireProtocolTests.swift on purpose: the Xcode
/// project file lists exactly three test files (WireProtocolTests,
/// PreRollBufferTests, SessionStateTests), so the enrollment tests
/// ride along in this file instead of adding a fourth file the
/// project does not reference.
final class EnrollmentWireTests: XCTestCase {

    // MARK: - Helpers

    private func makePCM(byteCount: Int) -> Data {
        Data((0..<byteCount).map { UInt8($0 % 251) })
    }

    /// A canonical 44 byte WAV header (RIFF/WAVE, PCM, mono, 16 bit)
    /// for the given payload size, matching what the old Revision 2
    /// client used to send.
    private func wavHeader(payloadBytes: Int, sampleRate: Int = 16000) -> Data {
        var out = Data()
        func ascii(_ s: String) { out.append(s.data(using: .ascii)!) }
        func u16(_ v: UInt16) {
            var x = v.littleEndian; out.append(Data(bytes: &x, count: 2))
        }
        func u32(_ v: UInt32) {
            var x = v.littleEndian; out.append(Data(bytes: &x, count: 4))
        }
        ascii("RIFF"); u32(UInt32(36 + payloadBytes)); ascii("WAVE")
        ascii("fmt "); u32(16); u16(1); u16(1)
        u32(UInt32(sampleRate)); u32(UInt32(sampleRate) * 2)
        u16(2); u16(16)
        ascii("data"); u32(UInt32(payloadBytes))
        return out
    }

    // MARK: - WAV header strip

    func testStrips44ByteWAVHeader() {
        let pcm = makePCM(byteCount: 3200)
        let wav = wavHeader(payloadBytes: pcm.count) + pcm
        let raw = EnrollmentAPI.rawPCMStrippingWAVHeader(wav)
        XCTAssertEqual(raw, pcm)
    }

    func testRawPCMWithoutHeaderPassesThrough() {
        let pcm = makePCM(byteCount: 3200)
        let raw = EnrollmentAPI.rawPCMStrippingWAVHeader(pcm)
        XCTAssertEqual(raw, pcm)
    }

    func testShortDataPassesThrough() {
        let tiny = makePCM(byteCount: 40)
        XCTAssertEqual(EnrollmentAPI.rawPCMStrippingWAVHeader(tiny), tiny)
        XCTAssertEqual(EnrollmentAPI.rawPCMStrippingWAVHeader(Data()), Data())
    }

    func testNonCanonicalDataPassesThrough() {
        // Not RIFF: untouched even when longer than 44 bytes.
        var data = makePCM(byteCount: 100)
        data[0] = 0x00; data[1] = 0x00; data[2] = 0x00; data[3] = 0x00
        XCTAssertEqual(EnrollmentAPI.rawPCMStrippingWAVHeader(data), data)
    }

    // MARK: - Endpoint

    func testEnrollmentEndpointIsAPIPEnroll() {
        // XCODE-VERIFY: this asserts the new JSON contract path. If the
        // server moves enrollment, update Config.enrollmentURL and this
        // test together.
        XCTAssertEqual(Config.enrollmentURL.path, "/api/enroll")
        let api = EnrollmentAPI(
            baseURL: URL(string: "http://192.168.2.1:8000")!)
        XCTAssertEqual(api.enrollmentEndpoint.path, "/api/enroll")
        let custom = EnrollmentAPI(
            baseURL: URL(string: "http://10.0.0.5:9000")!)
        XCTAssertEqual(custom.enrollmentEndpoint.absoluteString,
                       "http://10.0.0.5:9000/api/enroll")
    }
}
