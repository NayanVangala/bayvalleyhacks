//
//  WireProtocol.swift
//  InsightBridge
//
//  OnRecord wire protocol v1.
//
//  Two frame families share one WebSocket:
//  - Text frames: JSON control messages (see ControlMessage / ServerMessage).
//  - Binary frames: 16 byte header + payload (see BinaryFrameHeader).
//
//  Binary header layout, exactly 16 bytes:
//    bytes 0-1:   magic 0x4F 0x52 ("OR")
//    byte  2:     kind (1 = audio PCM16, 2 = photo JPEG)
//    byte  3:     flags (bit0 0x01 = pre-roll audio, bit1 0x02 = final frame of segment)
//    bytes 4-7:   segIdx UInt32 big-endian (segment index; photos use 0xFFFFFFFF)
//    bytes 8-11:  frameSeq UInt32 big-endian (per segment for audio, global for photos)
//    bytes 12-15: captureTsMs UInt32 big-endian (ms since session start)
//
//  Pure Foundation. No third party dependencies.
//

import Foundation

// MARK: - Binary frame header

public enum FrameKind: UInt8, Equatable {
    case audio = 1
    case photo = 2
}

public enum FrameFlags {
    /// bit0: this audio frame belongs to the pre-roll buffer.
    public static let preRoll: UInt8 = 0x01
    /// bit1: this is the final frame of its segment.
    public static let final: UInt8 = 0x02
}

public struct BinaryFrameHeader: Equatable {
    public static let headerLength = 16
    public static let magicByte0: UInt8 = 0x4F // "O"
    public static let magicByte1: UInt8 = 0x52 // "R"
    /// segIdx value used for every photo frame.
    public static let photoSegIdx: UInt32 = 0xFFFF_FFFF

    public var kind: FrameKind
    public var flags: UInt8
    public var segIdx: UInt32
    public var frameSeq: UInt32
    public var captureTsMs: UInt32

    public init(kind: FrameKind, flags: UInt8, segIdx: UInt32, frameSeq: UInt32, captureTsMs: UInt32) {
        self.kind = kind
        self.flags = flags
        self.segIdx = segIdx
        self.frameSeq = frameSeq
        self.captureTsMs = captureTsMs
    }

    public var isPreRoll: Bool { flags & FrameFlags.preRoll != 0 }
    public var isFinal: Bool { flags & FrameFlags.final != 0 }

    /// Encodes the header as exactly 16 bytes, all integers big-endian.
    public func encode() -> Data {
        var data = Data()
        data.reserveCapacity(Self.headerLength)
        data.append(Self.magicByte0)
        data.append(Self.magicByte1)
        data.append(kind.rawValue)
        data.append(flags)
        Self.appendBigEndian(segIdx, to: &data)
        Self.appendBigEndian(frameSeq, to: &data)
        Self.appendBigEndian(captureTsMs, to: &data)
        return data
    }

    /// Decodes a header from the first 16 bytes of `data`.
    /// Returns nil when the data is too short, the magic is wrong,
    /// or the kind byte is not a known FrameKind.
    public static func decode(_ data: Data) -> BinaryFrameHeader? {
        guard data.count >= headerLength else { return nil }
        let bytes = [UInt8](data.prefix(headerLength))
        guard bytes[0] == magicByte0, bytes[1] == magicByte1 else { return nil }
        guard let kind = FrameKind(rawValue: bytes[2]) else { return nil }
        let segIdx = readBigEndian(bytes, offset: 4)
        let frameSeq = readBigEndian(bytes, offset: 8)
        let captureTsMs = readBigEndian(bytes, offset: 12)
        return BinaryFrameHeader(
            kind: kind,
            flags: bytes[3],
            segIdx: segIdx,
            frameSeq: frameSeq,
            captureTsMs: captureTsMs
        )
    }

    /// Header bytes followed by the payload, ready to send as one binary frame.
    public func encodedFrame(payload: Data) -> Data {
        var data = encode()
        data.append(payload)
        return data
    }

    private static func appendBigEndian(_ value: UInt32, to data: inout Data) {
        data.append(UInt8((value >> 24) & 0xFF))
        data.append(UInt8((value >> 16) & 0xFF))
        data.append(UInt8((value >> 8) & 0xFF))
        data.append(UInt8(value & 0xFF))
    }

    private static func readBigEndian(_ bytes: [UInt8], offset: Int) -> UInt32 {
        (UInt32(bytes[offset]) << 24)
            | (UInt32(bytes[offset + 1]) << 16)
            | (UInt32(bytes[offset + 2]) << 8)
            | UInt32(bytes[offset + 3])
    }
}

// MARK: - Control messages (client to server, JSON text frames)

public struct ControlMessage: Codable, Equatable {

    public struct AudioFormat: Codable, Equatable {
        public var codec: String
        public var sampleRate: Int
        public var channels: Int

        public init(codec: String, sampleRate: Int, channels: Int) {
            self.codec = codec
            self.sampleRate = sampleRate
            self.channels = channels
        }

        public static let pcm16 = AudioFormat(codec: "pcm16", sampleRate: 16000, channels: 1)

        enum CodingKeys: String, CodingKey {
            case codec
            case sampleRate = "sample_rate"
            case channels
        }
    }

    public struct SessionStats: Codable, Equatable {
        public var photosSent: Int
        public var audioSeconds: Double
        public var framesSent: Int
        public var chunksReplayed: Int
        /// Frames evicted from offline staging by the ring cap.
        /// Reported so drops are accounted for, never silent.
        public var chunksDropped: Int

        public init(photosSent: Int, audioSeconds: Double, framesSent: Int, chunksReplayed: Int, chunksDropped: Int = 0) {
            self.photosSent = photosSent
            self.audioSeconds = audioSeconds
            self.framesSent = framesSent
            self.chunksReplayed = chunksReplayed
            self.chunksDropped = chunksDropped
        }

        enum CodingKeys: String, CodingKey {
            case photosSent = "photos_sent"
            case audioSeconds = "audio_seconds"
            case framesSent = "frames_sent"
            case chunksReplayed = "chunks_replayed"
            case chunksDropped = "chunks_dropped"
        }
    }

    public static let currentProtocolVersion = 1
    public static let appName = "InsightBridge"

    public var type: String
    // hello
    public var protocolVersion: Int?
    public var app: String?
    public var appVersion: String?
    public var deviceID: String?
    public var glassesModel: String?
    public var transport: String?
    public var audioSource: String?
    public var resumeSessionID: String?
    // shared session fields
    public var sessionID: String?
    // session_start
    public var startedAt: String?
    public var talkMode: String?
    // talk_start / talk_end
    public var segIdx: UInt32?
    public var t0MonoMs: UInt64?
    public var t1MonoMs: UInt64?
    public var audioFormat: AudioFormat?
    public var durationS: Double?
    public var framesSent: Int?
    // photo_meta
    public var photoID: String?
    public var seq: UInt32?
    public var capturedAt: String?
    public var eager: Bool?
    public var bytes: Int?
    // tag_moment / ping
    public var tMonoMs: UInt64?
    public var source: String?
    // session_end
    public var endedAt: String?
    public var reason: String?
    public var stats: SessionStats?

    public init(type: String) {
        self.type = type
    }

    enum CodingKeys: String, CodingKey {
        case type
        case protocolVersion = "protocol"
        case app
        case appVersion = "app_version"
        case deviceID = "device_id"
        case glassesModel = "glasses_model"
        case transport
        case audioSource = "audio_source"
        case resumeSessionID = "resume_session_id"
        case sessionID = "session_id"
        case startedAt = "started_at"
        case talkMode = "talk_mode"
        case segIdx = "seg_idx"
        case t0MonoMs = "t0_mono_ms"
        case t1MonoMs = "t1_mono_ms"
        case audioFormat = "audio_format"
        case durationS = "duration_s"
        case framesSent = "frames_sent"
        case photoID = "photo_id"
        case seq
        case capturedAt = "captured_at"
        case eager
        case bytes
        case tMonoMs = "t_mono_ms"
        case source
        case endedAt = "ended_at"
        case reason
        case stats
    }

    // MARK: Encoding helpers

    public func jsonData() throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(self)
    }

    public func jsonString() -> String? {
        guard let data = try? jsonData() else { return nil }
        return String(data: data, encoding: .utf8)
    }

    public static func iso8601String(from date: Date) -> String {
        iso8601Formatter.string(from: date)
    }

    private static let iso8601Formatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    // MARK: Static constructors

    public static func hello(
        deviceID: String,
        glassesModel: String,
        transport: String,
        audioSource: String,
        resumeSessionID: UUID? = nil,
        appVersion: String = "1.0.0"
    ) -> ControlMessage {
        var message = ControlMessage(type: "hello")
        message.protocolVersion = currentProtocolVersion
        message.app = appName
        message.appVersion = appVersion
        message.deviceID = deviceID
        message.glassesModel = glassesModel
        message.transport = transport
        message.audioSource = audioSource
        message.resumeSessionID = resumeSessionID?.uuidString
        return message
    }

    public static func sessionStart(sessionID: UUID, startedAt: Date, talkMode: String) -> ControlMessage {
        sessionStart(sessionID: sessionID, startedAt: iso8601String(from: startedAt), talkMode: talkMode)
    }

    public static func sessionStart(sessionID: UUID, startedAt: String, talkMode: String) -> ControlMessage {
        var message = ControlMessage(type: "session_start")
        message.sessionID = sessionID.uuidString
        message.startedAt = startedAt
        message.talkMode = talkMode
        return message
    }

    public static func talkStart(sessionID: UUID, segIdx: UInt32, t0MonoMs: UInt64) -> ControlMessage {
        var message = ControlMessage(type: "talk_start")
        message.sessionID = sessionID.uuidString
        message.segIdx = segIdx
        message.t0MonoMs = t0MonoMs
        message.audioFormat = .pcm16
        return message
    }

    public static func talkEnd(
        sessionID: UUID,
        segIdx: UInt32,
        t1MonoMs: UInt64,
        durationS: Double,
        framesSent: Int
    ) -> ControlMessage {
        var message = ControlMessage(type: "talk_end")
        message.sessionID = sessionID.uuidString
        message.segIdx = segIdx
        message.t1MonoMs = t1MonoMs
        message.durationS = durationS
        message.framesSent = framesSent
        return message
    }

    public static func photoMeta(
        sessionID: UUID,
        photoID: String,
        seq: UInt32,
        capturedAt: Date,
        eager: Bool,
        bytes: Int
    ) -> ControlMessage {
        photoMeta(
            sessionID: sessionID,
            photoID: photoID,
            seq: seq,
            capturedAt: iso8601String(from: capturedAt),
            eager: eager,
            bytes: bytes
        )
    }

    public static func photoMeta(
        sessionID: UUID,
        photoID: String,
        seq: UInt32,
        capturedAt: String,
        eager: Bool,
        bytes: Int
    ) -> ControlMessage {
        var message = ControlMessage(type: "photo_meta")
        message.sessionID = sessionID.uuidString
        message.photoID = photoID
        message.seq = seq
        message.capturedAt = capturedAt
        message.eager = eager
        message.bytes = bytes
        return message
    }

    public static func tagMoment(sessionID: UUID, tMonoMs: UInt64, source: String) -> ControlMessage {
        var message = ControlMessage(type: "tag_moment")
        message.sessionID = sessionID.uuidString
        message.tMonoMs = tMonoMs
        message.source = source
        return message
    }

    public static func sessionPause(sessionID: UUID) -> ControlMessage {
        var message = ControlMessage(type: "session_pause")
        message.sessionID = sessionID.uuidString
        return message
    }

    public static func sessionResume(sessionID: UUID) -> ControlMessage {
        var message = ControlMessage(type: "session_resume")
        message.sessionID = sessionID.uuidString
        return message
    }

    public static func sessionEnd(sessionID: UUID, endedAt: Date, reason: String, stats: SessionStats) -> ControlMessage {
        sessionEnd(sessionID: sessionID, endedAt: iso8601String(from: endedAt), reason: reason, stats: stats)
    }

    public static func sessionEnd(sessionID: UUID, endedAt: String, reason: String, stats: SessionStats) -> ControlMessage {
        var message = ControlMessage(type: "session_end")
        message.sessionID = sessionID.uuidString
        message.endedAt = endedAt
        message.reason = reason
        message.stats = stats
        return message
    }

    public static func ping(sessionID: UUID?, tMonoMs: UInt64) -> ControlMessage {
        var message = ControlMessage(type: "ping")
        message.sessionID = sessionID?.uuidString
        message.tMonoMs = tMonoMs
        return message
    }
}

// MARK: - Server messages (server to client, JSON text frames)

public enum ServerMessage: Equatable {
    case welcome(serverTime: String?, ok: Bool)
    case pong(tMonoMs: UInt64, serverTime: String?)
    case captureNow(photoID: String)
    case error(code: String, message: String, fatal: Bool)
    case unknown(String)

    /// Parses one JSON text frame from the server.
    /// Anything unrecognized, malformed, or missing required fields
    /// comes back as .unknown with the raw text preserved.
    public static func parse(_ text: String) -> ServerMessage {
        guard let data = text.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data),
              let dict = object as? [String: Any],
              let type = dict["type"] as? String
        else {
            return .unknown(text)
        }

        switch type {
        case "welcome":
            return .welcome(serverTime: stringValue(dict["server_time"]), ok: boolValue(dict["ok"]) ?? true)
        case "pong":
            return .pong(
                tMonoMs: uint64Value(dict["t_mono_ms"]) ?? 0,
                serverTime: stringValue(dict["server_time"])
            )
        case "capture_now":
            guard let photoID = dict["photo_id"] as? String, !photoID.isEmpty else {
                return .unknown(text)
            }
            return .captureNow(photoID: photoID)
        case "error":
            return .error(
                code: dict["code"] as? String ?? "unknown",
                message: dict["message"] as? String ?? "",
                fatal: boolValue(dict["fatal"]) ?? false
            )
        default:
            return .unknown(text)
        }
    }

    private static func stringValue(_ value: Any?) -> String? {
        if let string = value as? String { return string }
        if let number = value as? NSNumber { return number.stringValue }
        return nil
    }

    private static func boolValue(_ value: Any?) -> Bool? {
        if let bool = value as? Bool { return bool }
        if let number = value as? NSNumber { return number.boolValue }
        return nil
    }

    private static func uint64Value(_ value: Any?) -> UInt64? {
        if let number = value as? NSNumber { return number.uint64Value }
        if let string = value as? String { return UInt64(string) }
        return nil
    }
}
