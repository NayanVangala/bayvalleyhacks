// Lexia Read Mode for OnRecord. Uncompiled Swift source written Oct 2026. Bridge team: copy into the bridge-ios target and wire the marked integration points.
//
// LensChunkDisplayModel.swift
// Display model for the glasses lens.
// Design rule: exactly one chunk is visible at a time. The lens is small
// and the reader should never have to scan a page or scroll. Navigation
// moves one chunk forward or back and nothing else changes on screen.

import Foundation
import Combine

@MainActor
public final class LensChunkDisplayModel: ObservableObject {

    // MARK: - Published state

    @Published public private(set) var chunks: [ReadChunk] = []
    @Published public private(set) var currentIndex: Int = 0
    @Published public private(set) var currentChunk: ReadChunk?
    @Published public var isSpeaking: Bool = false

    // MARK: - Session state

    /// Session id from the most recent ReadResponse, needed for speak calls.
    @Published public private(set) var sessionID: String?

    /// Full recognized text from the most recent ReadResponse.
    @Published public private(set) var fullText: String = ""

    /// Mean OCR confidence from the most recent ReadResponse, 0 to 1 when provided by the API.
    @Published public private(set) var meanConfidence: Double = 0

    public init() {}

    // MARK: - Derived values

    public var chunkCount: Int { chunks.count }

    public var hasChunks: Bool { !chunks.isEmpty }

    public var isAtStart: Bool { currentIndex <= 0 }

    public var isAtEnd: Bool { chunks.isEmpty || currentIndex >= chunks.count - 1 }

    /// One based position label for the lens and the phone, for example "3 of 12".
    public var positionLabel: String {
        guard !chunks.isEmpty else { return "0 of 0" }
        return "\(currentIndex + 1) of \(chunks.count)"
    }

    // MARK: - Loading

    /// Loads a ReadResponse and resets navigation to the first chunk.
    public func load(_ response: ReadResponse) {
        sessionID = response.sessionID
        fullText = response.text
        meanConfidence = response.meanConfidence
        chunks = response.chunks.sorted { $0.index < $1.index }
        currentIndex = 0
        currentChunk = chunks.first
        isSpeaking = false
    }

    /// Clears all chunks and session state, for example when Read Mode exits.
    public func clear() {
        sessionID = nil
        fullText = ""
        meanConfidence = 0
        chunks = []
        currentIndex = 0
        currentChunk = nil
        isSpeaking = false
    }

    // MARK: - Navigation

    /// Advances to the next chunk. Does nothing at the end or when empty.
    public func next() {
        guard !chunks.isEmpty, currentIndex < chunks.count - 1 else { return }
        currentIndex += 1
        currentChunk = chunks[currentIndex]
    }

    /// Moves to the previous chunk. Does nothing at the start or when empty.
    public func previous() {
        guard !chunks.isEmpty, currentIndex > 0 else { return }
        currentIndex -= 1
        currentChunk = chunks[currentIndex]
    }

    // MARK: - Timing estimate

    /// Estimated reading or listening time for a chunk in seconds.
    /// Uses word_count divided by 2.6 words per second, which is about
    /// 156 words per minute. This is an estimate for pacing and progress
    /// display only, not a measured duration. For spoken audio, prefer
    /// SpeakResponse.durationSeconds when it is available.
    public func estimatedSeconds(for chunk: ReadChunk) -> Double {
        guard chunk.wordCount > 0 else { return 0 }
        return Double(chunk.wordCount) / 2.6
    }

    /// Estimated seconds for the currently visible chunk.
    public var estimatedSecondsForCurrentChunk: Double {
        guard let chunk = currentChunk else { return 0 }
        return estimatedSeconds(for: chunk)
    }

    /// Estimated seconds for all chunks combined.
    public var estimatedTotalSeconds: Double {
        chunks.reduce(0) { $0 + estimatedSeconds(for: $1) }
    }
}
