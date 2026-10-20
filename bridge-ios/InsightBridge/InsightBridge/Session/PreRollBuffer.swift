import Foundation

/// Fixed-capacity ring buffer of raw PCM bytes used for push-to-talk pre-roll.
///
/// While a session is armed, live audio is appended here continuously. On a
/// talk press the buffer is drained and its contents become the first frames
/// of the new segment, so speech right before the press is not lost.
///
/// Contract notes:
/// - Capacity is expressed in bytes. At the shared audio format (PCM16 mono,
///   16 kHz, 2 bytes per sample) 10 seconds is 10 * 16000 * 2 = 320,000 bytes.
/// - The buffer stores raw bytes only. It is partial-frame safe: `drain()`
///   returns whatever is buffered, oldest byte first. Frame alignment is the
///   caller's job; in practice chunks arrive frame-aligned (3200 bytes).
/// - Pure Foundation, no timers, no I/O. All public entry points are
///   thread-safe via `NSLock`, so the audio tap and the UI can call in from
///   different queues.
final class PreRollBuffer {

    /// Maximum number of bytes retained. Older bytes are overwritten once full.
    let capacityBytes: Int

    /// Bytes per second of the shared audio format: 16000 samples * 2 bytes.
    private static let bytesPerSecond = 32_000

    private var storage: [UInt8]
    /// Index of the oldest buffered byte inside `storage`.
    private var head: Int = 0
    /// Number of valid bytes currently buffered.
    private var count: Int = 0

    private let lock = NSLock()

    init(capacityBytes: Int) {
        let clamped = max(0, capacityBytes)
        self.capacityBytes = clamped
        self.storage = [UInt8](repeating: 0, count: clamped)
    }

    /// Bytes currently buffered, from 0 up to `capacityBytes`.
    var bufferedBytes: Int {
        lock.lock()
        defer { lock.unlock() }
        return count
    }

    /// Duration of the buffered audio in seconds at the shared audio format.
    var durationSeconds: Double {
        lock.lock()
        defer { lock.unlock() }
        guard Self.bytesPerSecond > 0 else { return 0 }
        return Double(count) / Double(Self.bytesPerSecond)
    }

    /// Append raw PCM bytes. When the buffer is full, the oldest bytes are
    /// overwritten. Appending more than the capacity keeps only the newest
    /// `capacityBytes` bytes. Empty appends are a no-op.
    func append(_ data: Data) {
        guard !data.isEmpty, capacityBytes > 0 else { return }
        lock.lock()
        defer { lock.unlock() }

        if data.count >= capacityBytes {
            // Keep only the newest capacityBytes bytes, stored contiguously.
            let tail = data.suffix(capacityBytes)
            storage = Array(tail)
            head = 0
            count = capacityBytes
            return
        }

        for byte in data {
            if count < capacityBytes {
                storage[(head + count) % capacityBytes] = byte
                count += 1
            } else {
                storage[head] = byte
                head = (head + 1) % capacityBytes
            }
        }
    }

    /// Return all buffered audio oldest-to-newest and clear the buffer.
    /// Returns empty `Data` when nothing is buffered.
    func drain() -> Data {
        lock.lock()
        defer { lock.unlock() }
        guard count > 0, capacityBytes > 0 else { return Data() }

        var out = [UInt8]()
        out.reserveCapacity(count)
        for offset in 0..<count {
            out.append(storage[(head + offset) % capacityBytes])
        }
        resetLocked()
        return Data(out)
    }

    /// Discard all buffered audio. Used when a session pauses: paused audio
    /// must never leak into a later segment.
    func clear() {
        lock.lock()
        defer { lock.unlock() }
        resetLocked()
    }

    /// Reset read state and wipe stored samples. Caller must hold `lock`.
    private func resetLocked() {
        head = 0
        count = 0
        if !storage.isEmpty {
            storage = [UInt8](repeating: 0, count: capacityBytes)
        }
    }
}
