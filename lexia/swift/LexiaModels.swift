// Lexia Read Mode for OnRecord. Uncompiled Swift source written Oct 2026. Bridge team: copy into the bridge-ios target and wire the marked integration points.
//
// LexiaModels.swift
// Codable models matching the standalone Lexia API on port 8100.
// API contract:
//   POST /read  {"image_base64": str, "simplify": bool}
//     -> {"session_id", "text", "chunks": [{"index", "text", "word_count"}], "mean_confidence", "engine"}
//   POST /read/{session_id}/speak
//     -> {"audio_url", "duration_seconds", "engine"}
//   GET /health
//     -> {"status", "ocr_engine", "tts_engine"}

import Foundation

// MARK: - Read

public struct ReadChunk: Codable, Equatable, Identifiable {
    public let index: Int
    public let text: String
    public let wordCount: Int

    public var id: Int { index }

    enum CodingKeys: String, CodingKey {
        case index
        case text
        case wordCount = "word_count"
    }

    public init(index: Int, text: String, wordCount: Int) {
        self.index = index
        self.text = text
        self.wordCount = wordCount
    }
}

public struct ReadResponse: Codable, Equatable {
    public let sessionID: String
    public let text: String
    public let chunks: [ReadChunk]
    public let meanConfidence: Double
    public let engine: String

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case text
        case chunks
        case meanConfidence = "mean_confidence"
        case engine
    }

    public init(sessionID: String, text: String, chunks: [ReadChunk], meanConfidence: Double, engine: String) {
        self.sessionID = sessionID
        self.text = text
        self.chunks = chunks
        self.meanConfidence = meanConfidence
        self.engine = engine
    }
}

// MARK: - Speak

public struct SpeakResponse: Codable, Equatable {
    public let audioURL: String
    public let durationSeconds: Double
    public let engine: String

    enum CodingKeys: String, CodingKey {
        case audioURL = "audio_url"
        case durationSeconds = "duration_seconds"
        case engine
    }

    public init(audioURL: String, durationSeconds: Double, engine: String) {
        self.audioURL = audioURL
        self.durationSeconds = durationSeconds
        self.engine = engine
    }
}

// MARK: - Health

public struct HealthResponse: Codable, Equatable {
    public let status: String
    public let ocrEngine: String
    public let ttsEngine: String

    enum CodingKeys: String, CodingKey {
        case status
        case ocrEngine = "ocr_engine"
        case ttsEngine = "tts_engine"
    }

    public init(status: String, ocrEngine: String, ttsEngine: String) {
        self.status = status
        self.ocrEngine = ocrEngine
        self.ttsEngine = ttsEngine
    }
}

// MARK: - Errors

public enum LexiaError: Error, Equatable {
    case badURL
    case network
    case server(status: Int)
    case decoding
    case noSession
}

extension LexiaError: LocalizedError {
    public var errorDescription: String? {
        switch self {
        case .badURL:
            return "The Lexia base URL is invalid."
        case .network:
            return "Could not reach the Lexia service."
        case .server(let status):
            return "Lexia service returned HTTP \(status)."
        case .decoding:
            return "Could not decode the Lexia response."
        case .noSession:
            return "No active Lexia reading session."
        }
    }
}
