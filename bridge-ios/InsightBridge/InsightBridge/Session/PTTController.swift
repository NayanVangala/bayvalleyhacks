import Combine
import Foundation

/// Push-to-talk segmenter for the OnRecord capture pipeline.
///
/// One press is one segment. While the session is armed (not talking, not
/// paused) fed audio goes into the pre-roll ring buffer. On `pressBegan()`
/// the pre-roll drains as the first frames of the new segment, flagged as
/// pre-roll on the wire, so speech just before the press is kept. While
/// talking, fed audio is emitted live as exact 3200-byte frames. On
/// `pressEnded()` the open segment is sealed with a final-flagged frame.
///
/// This type owns framing only. Session lifecycle and state transitions live
/// in SessionManager (SessionStateMachine); SessionManager wires the frame
/// sink to WSClient via `attach(frameSink:)` and supplies the session clock
/// via `setClock(_:)`.
///
/// Wire flags (WireProtocol, worker 6): bit0 (0x01) means the frame is
/// pre-roll audio, bit1 (0x02) means the frame is the final frame of a
/// segment. A frame can carry both bits only in theory; this controller
/// never sets both, because a pre-roll partial is padded and emitted at
/// press time, and the final flag is added later at release time.
///
/// Threading: all public methods are serialized with an internal lock, so
/// `feed(_:)` may arrive from the audio queue while press events arrive from
/// the UI. The frame sink is invoked while the lock is held so frame order
/// is strict (pre-roll always precedes live audio); the sink must therefore
/// be fast, non-blocking, and must not call back into this controller.
final class PTTController: ObservableObject {

    /// Counters SessionManager reads at session end for the stats payload.
    struct Stats: Equatable {
        var segmentsSealed: Int = 0
        var segmentsDiscarded: Int = 0
        var framesSent: Int = 0
    }

    // MARK: - Wire constants

    /// flags bit0: frame is pre-roll audio.
    static let flagPreRoll: UInt8 = 0x01
    /// flags bit1: final frame of a segment.
    static let flagFinal: UInt8 = 0x02

    /// Exact wire frame size in bytes (AudioFormat.frameBytes).
    private static let frameBytes = 3_200
    /// Bytes per second of audio (AudioFormat.sampleRate 16000 * 2 bytes).
    private static let bytesPerSecond = 32_000
    /// Duration of one wire frame in milliseconds (AudioFormat.frameMs).
    private static let frameDurationMs: UInt32 = 100
    /// Segments with less total audio than this are discarded (plan rule).
    private static let minimumSegmentMs = 300
    /// Same rule in bytes: 300 ms * 32000 bytes/s / 1000 = 9600 bytes.
    private static let minimumSegmentBytes = minimumSegmentMs * bytesPerSecond / 1_000

    // MARK: - Published UI state

    /// Smoothed mic level, 0...1, from the PCM16 RMS of recent chunks.
    @Published private(set) var audioLevel: Float = 0
    /// True between a kept `pressBegan()` and its `pressEnded()`.
    @Published private(set) var isTalking: Bool = false
    /// Index of the segment currently open, or of the next segment when
    /// idle. Advances only when a segment is sealed and kept.
    @Published private(set) var currentSegmentIndex: UInt32 = 0

    /// Session counters, readable by SessionManager. Updated under the lock.
    private(set) var stats = Stats()

    /// Called with the segment index when a press ends with less than
    /// 300 ms of total audio (pre-roll plus live). The frames were already
    /// streamed, so SessionManager uses this to tell the server to drop the
    /// segment. Fired after the final frame has been emitted, and never
    /// while the internal lock is held.
    var onSegmentDiscarded: ((UInt32) -> Void)?

    /// True while paused. While paused, fed audio is dropped, the pre-roll
    /// stays empty, and presses are ignored.
    private(set) var isPaused: Bool = false

    // MARK: - Private state

    private let preRoll: PreRollBuffer
    private let lock = NSLock()

    private var frameSink: ((_ segIdx: UInt32, _ frameSeq: UInt32, _ captureTsMs: UInt32, _ flags: UInt8, _ pcm: Data) -> Void)?

    /// Milliseconds since session start. SessionManager replaces this via
    /// `setClock(_:)`; the default falls back to process uptime in ms, which
    /// is monotonic but not session-relative.
    private var clock: () -> UInt32 = {
        UInt32(ProcessInfo.processInfo.systemUptime * 1_000)
    }

    /// Leftover live bytes that do not yet fill a whole frame.
    private var pending = Data()
    /// Next frame sequence number inside the open segment. Starts at 0.
    private var segmentFrameSeq: UInt32 = 0
    /// Real audio bytes attributed to the open segment (pre-roll plus live,
    /// padding never counted). Drives the 300 ms minimum rule.
    private var segmentAudioBytes: Int = 0
    /// Frames emitted for the open segment so far.
    private var segmentFramesEmitted: Int = 0

    // MARK: - Init and wiring

    init(preRoll: PreRollBuffer = PreRollBuffer(capacityBytes: 320_000)) {
        self.preRoll = preRoll
    }

    /// Wire the frame output. SessionManager points this at WSClient.
    /// Frames emitted before a sink is attached are counted in `stats` and
    /// dropped.
    func attach(frameSink: @escaping (_ segIdx: UInt32, _ frameSeq: UInt32, _ captureTsMs: UInt32, _ flags: UInt8, _ pcm: Data) -> Void) {
        lock.lock()
        defer { lock.unlock() }
        self.frameSink = frameSink
    }

    /// Provide the session clock: milliseconds since session start.
    func setClock(_ clock: @escaping () -> UInt32) {
        lock.lock()
        defer { lock.unlock() }
        self.clock = clock
    }

    // MARK: - Audio intake

    /// Feed one captured chunk from any audio source.
    ///
    /// - Paused: the chunk is dropped entirely.
    /// - Armed (not talking): the chunk fills the pre-roll buffer.
    /// - Talking: the chunk is framed and emitted live. Bytes are merged
    ///   with the pending remainder and split into exact 3200-byte frames,
    ///   so chunk sizes do not need to match the wire frame size.
    ///
    /// Every non-paused chunk also updates `audioLevel`.
    func feed(_ chunk: AudioChunk) {
        lock.lock()
        defer { lock.unlock() }

        guard !isPaused else { return }

        updateLevelLocked(with: chunk.pcm)

        if isTalking {
            segmentAudioBytes += chunk.pcm.count
            pending.append(chunk.pcm)
            while pending.count >= Self.frameBytes {
                let frame = Data(pending.prefix(Self.frameBytes))
                pending.removeFirst(Self.frameBytes)
                emitLocked(flags: 0, pcm: frame, captureTsMs: clock())
            }
        } else {
            preRoll.append(chunk.pcm)
        }
    }

    // MARK: - Press handling

    /// Start a segment. Ignored when already talking or while paused.
    ///
    /// The pre-roll drains first and is emitted as the opening frames of the
    /// new segment with the pre-roll flag set. Pre-roll timestamps are
    /// reconstructed backwards from the press time, one frame duration per
    /// frame, so they stay monotonic and land just before the press.
    func pressBegan() {
        lock.lock()
        defer { lock.unlock() }

        guard !isTalking, !isPaused else { return }

        let drained = preRoll.drain()

        segmentFrameSeq = 0
        segmentFramesEmitted = 0
        segmentAudioBytes = drained.count
        pending.removeAll()
        isTalking = true

        guard !drained.isEmpty else { return }

        // Pre-roll normally holds whole frames because feed appends whole
        // chunks. A trailing partial can still occur if a source ever sends
        // a non-frame-aligned chunk while armed; it is zero-padded to a
        // full frame here, and the padding is not counted in
        // segmentAudioBytes, so duration checks use real audio only.
        let frameCount = (drained.count + Self.frameBytes - 1) / Self.frameBytes
        let pressTsMs = clock()
        for index in 0..<frameCount {
            let start = index * Self.frameBytes
            let end = min(start + Self.frameBytes, drained.count)
            var frame = Data(drained[start..<end])
            if frame.count < Self.frameBytes {
                frame.append(Data(count: Self.frameBytes - frame.count))
            }
            let framesAfterThis = UInt32(frameCount - 1 - index)
            let backMs = framesAfterThis * Self.frameDurationMs
            let captureTsMs = pressTsMs >= backMs ? pressTsMs - backMs : 0
            emitLocked(flags: Self.flagPreRoll, pcm: frame, captureTsMs: captureTsMs)
        }
    }

    /// End and seal the open segment. Ignored when not talking.
    ///
    /// A pending partial frame is zero-padded and emitted with the final
    /// flag. When no partial is pending but frames were emitted, one silent
    /// frame carrying only the final flag is emitted, so every kept segment
    /// ends with exactly one final-flagged frame on the wire.
    ///
    /// Minimum segment rule: if total audio (pre-roll plus live, padding
    /// excluded) is under 300 ms, the segment is discarded. It is not
    /// counted as sealed, `currentSegmentIndex` does not advance, and
    /// `onSegmentDiscarded` fires with its index. A press with no pre-roll
    /// and no live audio emits nothing at all and lands in the same
    /// discarded path.
    func pressEnded() {
        var discardedIndex: UInt32?

        lock.lock()
        if isTalking {
            isTalking = false
            let segmentIndex = currentSegmentIndex

            if !pending.isEmpty {
                var frame = pending
                frame.append(Data(count: Self.frameBytes - frame.count))
                pending.removeAll()
                emitLocked(flags: Self.flagFinal, pcm: frame, captureTsMs: clock())
            } else if segmentFramesEmitted > 0 {
                let silence = Data(count: Self.frameBytes)
                emitLocked(flags: Self.flagFinal, pcm: silence, captureTsMs: clock())
            }

            if segmentAudioBytes < Self.minimumSegmentBytes {
                stats.segmentsDiscarded += 1
                discardedIndex = segmentIndex
            } else {
                stats.segmentsSealed += 1
                currentSegmentIndex += 1
            }

            segmentFrameSeq = 0
            segmentFramesEmitted = 0
            segmentAudioBytes = 0
            pending.removeAll()
        }
        lock.unlock()

        if let discardedIndex {
            onSegmentDiscarded?(discardedIndex)
        }
    }

    // MARK: - Pause

    /// Pause or resume capture.
    ///
    /// Pausing zeroes the pre-roll buffer (plan rule) and drops fed audio
    /// until resumed, so segments never span a pause. Pausing mid-talk first
    /// seals the open segment exactly like `pressEnded()`, including the
    /// 300 ms minimum rule. Resuming leaves the pre-roll empty; it refills
    /// from the next fed chunks.
    func setPaused(_ paused: Bool) {
        if paused, isTalking {
            pressEnded()
        }

        lock.lock()
        isPaused = paused
        if paused {
            preRoll.clear()
            pending.removeAll()
            audioLevel = 0
        }
        lock.unlock()
    }

    /// Reset all segment state for a fresh session: index back to 0, stats
    /// zeroed, pre-roll cleared, not talking, not paused. The frame sink and
    /// clock stay wired. SessionManager calls this at session boundaries.
    func reset() {
        lock.lock()
        defer { lock.unlock() }
        isTalking = false
        isPaused = false
        currentSegmentIndex = 0
        stats = Stats()
        segmentFrameSeq = 0
        segmentFramesEmitted = 0
        segmentAudioBytes = 0
        pending.removeAll()
        audioLevel = 0
        preRoll.clear()
    }

    // MARK: - Internals (caller holds the lock)

    /// Emit one frame for the open segment and advance per-segment counters.
    private func emitLocked(flags: UInt8, pcm: Data, captureTsMs: UInt32) {
        let seq = segmentFrameSeq
        segmentFrameSeq += 1
        segmentFramesEmitted += 1
        stats.framesSent += 1
        frameSink?(currentSegmentIndex, seq, captureTsMs, flags, pcm)
    }

    /// Update the smoothed level from one chunk of PCM16 mono audio.
    /// RMS is normalized by full scale (32768). Attack is faster than
    /// release so speech onsets show quickly and the meter falls smoothly.
    private func updateLevelLocked(with pcm: Data) {
        let sampleCount = pcm.count / 2
        guard sampleCount > 0 else { return }

        var sumOfSquares: Double = 0
        pcm.withUnsafeBytes { raw in
            guard let base = raw.baseAddress else { return }
            let bytes = base.assumingMemoryBound(to: UInt8.self)
            for index in 0..<sampleCount {
                let lo = UInt16(bytes[index * 2])
                let hi = UInt16(bytes[index * 2 + 1])
                let sample = Int16(bitPattern: lo | (hi << 8))
                let value = Double(sample)
                sumOfSquares += value * value
            }
        }

        let rms = sqrt(sumOfSquares / Double(sampleCount)) / 32_768.0
        let instant = Float(min(1.0, max(0.0, rms)))
        let alpha: Float = instant > audioLevel ? 0.5 : 0.2
        audioLevel = min(1, max(0, audioLevel + (instant - audioLevel) * alpha))
    }
}
