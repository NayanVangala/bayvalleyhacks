//
//  OfflineStaging.swift
//  InsightBridge
//
//  Append-only on-disk staging for WebSocket payloads that cannot be
//  sent yet (laptop unreachable, venue Wi-Fi drop, mid-replay failure).
//  Nothing is silently dropped: items stay on disk until markSent is
//  called after a confirmed socket send, and the only deletion path
//  besides markSent is the ring cap, which counts every eviction in
//  `evictions` so the loss shows up in session stats as chunks_dropped.
//
//  Layout: Documents/staging/<sessionID>/
//    000000.json   control message (JSON text frame payload)
//    000001.bin    binary frame (16 byte header + payload)
//    000002.json   ...
//  Sequence numbers are global across both kinds so replay preserves
//  the original interleaving of control and frame traffic.
//
//  Pure Foundation. All public entry points are synchronous and
//  protected by one NSLock.
//

import Foundation

public final class OfflineStaging {

    public enum Kind: Equatable {
        case control
        case frame

        var fileExtension: String {
            switch self {
            case .control: return "json"
            case .frame: return "bin"
            }
        }

        init?(fileExtension ext: String) {
            switch ext {
            case "json": self = .control
            case "bin": self = .frame
            default: return nil
            }
        }
    }

    /// Ring cap: 500 MB of staged payload per session directory.
    public static let defaultCapBytes = 500 * 1024 * 1024

    public let sessionID: UUID
    public let directory: URL

    /// Cap enforced after every stage call. Lower it in tests if needed;
    /// change it before staging, not mid-session.
    public var capBytes: Int = OfflineStaging.defaultCapBytes

    /// Number of items deleted by the ring cap. Surfaced to the caller
    /// so session_end stats can report chunks_dropped honestly.
    public private(set) var evictions: Int = 0

    private let lock = NSLock()
    private let fileManager = FileManager.default
    private var nextSeq: Int = 0

    public init(sessionID: UUID, directory: URL? = nil) {
        self.sessionID = sessionID
        self.directory = directory ?? Self.defaultDirectory(sessionID: sessionID)
        try? fileManager.createDirectory(at: self.directory, withIntermediateDirectories: true)
        // Resume the sequence after any items left by a previous run so
        // a relaunched app never overwrites a pending staged file.
        let existing = scanLocked()
        if let maxSeq = existing.map(\.seq).max() {
            nextSeq = maxSeq + 1
        }
    }

    public static func defaultDirectory(sessionID: UUID) -> URL {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        return documents
            .appendingPathComponent("staging", isDirectory: true)
            .appendingPathComponent(sessionID.uuidString, isDirectory: true)
    }

    // MARK: - Staging

    public func stageControl(_ data: Data) {
        stage(data, kind: .control)
    }

    public func stageFrame(_ data: Data) {
        stage(data, kind: .frame)
    }

    private func stage(_ data: Data, kind: Kind) {
        lock.lock()
        defer { lock.unlock() }
        let url = directory.appendingPathComponent(fileName(seq: nextSeq, kind: kind))
        do {
            try data.write(to: url, options: [.atomic])
            nextSeq += 1
        } catch {
            NSLog("OfflineStaging: failed to write %@: %@", url.lastPathComponent, String(describing: error))
            return
        }
        enforceCapLocked()
    }

    // MARK: - Reading back

    /// All pending items in sequence order, ready to replay.
    public func pendingItems() -> [(seq: Int, kind: Kind, url: URL)] {
        lock.lock()
        defer { lock.unlock() }
        return scanLocked()
    }

    public var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return scanLocked().count
    }

    public func totalBytes() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return totalBytesLocked()
    }

    // MARK: - Removal

    /// Deletes the staged file for `seq` after a confirmed send.
    /// Unknown sequence numbers are ignored.
    public func markSent(seq: Int) {
        lock.lock()
        defer { lock.unlock() }
        for kind in [Kind.control, Kind.frame] {
            let url = directory.appendingPathComponent(fileName(seq: seq, kind: kind))
            if fileManager.fileExists(atPath: url.path) {
                try? fileManager.removeItem(at: url)
                return
            }
        }
    }

    /// Deletes every staged item. Does not reset the eviction counter.
    public func removeAll() {
        lock.lock()
        defer { lock.unlock() }
        for item in scanLocked() {
            try? fileManager.removeItem(at: item.url)
        }
    }

    // MARK: - Internals (call with lock held)

    private func fileName(seq: Int, kind: Kind) -> String {
        String(format: "%06d.%@", seq, kind.fileExtension)
    }

    private func scanLocked() -> [(seq: Int, kind: Kind, url: URL)] {
        guard let names = try? fileManager.contentsOfDirectory(atPath: directory.path) else {
            return []
        }
        var items: [(seq: Int, kind: Kind, url: URL)] = []
        for name in names {
            let url = directory.appendingPathComponent(name)
            let ext = url.pathExtension
            guard let kind = Kind(fileExtension: ext) else { continue }
            let stem = (name as NSString).deletingPathExtension
            guard let seq = Int(stem) else { continue }
            items.append((seq: seq, kind: kind, url: url))
        }
        return items.sorted { $0.seq < $1.seq }
    }

    private func totalBytesLocked() -> Int {
        var total = 0
        for item in scanLocked() {
            if let size = try? item.url.resourceValues(forKeys: [.fileSizeKey]).fileSize {
                total += size ?? 0
            }
        }
        return total
    }

    /// Evicts oldest items until the directory is back under the cap.
    /// The single newest item is always kept, even if it alone exceeds
    /// the cap, so the most recent payload is never the one discarded.
    private func enforceCapLocked() {
        while totalBytesLocked() > capBytes {
            let items = scanLocked()
            guard items.count > 1, let oldest = items.first else { return }
            do {
                try fileManager.removeItem(at: oldest.url)
                evictions += 1
            } catch {
                NSLog("OfflineStaging: failed to evict %@: %@", oldest.url.lastPathComponent, String(describing: error))
                return
            }
        }
    }
}
