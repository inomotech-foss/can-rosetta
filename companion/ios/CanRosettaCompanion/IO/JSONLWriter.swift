import Foundation
import os

/// A buffered, thread-safe writer that serialises `Encodable` records one per
/// line (newline-delimited JSON, `.jsonl`) to a file.
///
/// Used for `phone/motion.jsonl`, `phone/location.jsonl` and
/// `phone/video_index.jsonl`. All appends are funnelled through a private
/// serial queue, so it is safe to call `append` from the CoreMotion operation
/// queue, the CoreLocation delegate queue and the capture queue concurrently.
///
/// Records are encoded with `.convertToSnakeCase`, so idiomatic camelCase Swift
/// property names (`tUtc`, `hAcc`) map to the wire names the schemas require
/// (`t_utc`, `h_acc`).
final class JSONLWriter {

    private let handle: FileHandle
    private let queue: DispatchQueue
    private let encoder: JSONEncoder
    private let logger = Logger(subsystem: AppInfo.subsystem, category: "jsonl")

    /// Bytes buffered before a flush to disk. IMU at 100 Hz produces small
    /// lines; flushing in ~64 KiB chunks keeps syscall overhead low without
    /// risking much data on a crash.
    private let flushThreshold = 64 * 1024
    private var buffer = Data()

    /// Number of records written so far. Read via `rowCount` (thread-safe).
    private var count = 0

    let url: URL

    init(url: URL) throws {
        self.url = url
        FileManager.default.createFile(atPath: url.path, contents: nil)
        self.handle = try FileHandle(forWritingTo: url)
        self.queue = DispatchQueue(label: "\(AppInfo.subsystem).jsonl.\(url.lastPathComponent)")

        let enc = JSONEncoder()
        enc.keyEncodingStrategy = .convertToSnakeCase
        enc.outputFormatting = [.withoutEscapingSlashes] // compact, one line
        self.encoder = enc
    }

    /// Append one record. Non-blocking: encoding and buffering happen on the
    /// serial queue. Encoding failures are logged and the record is dropped
    /// (we never want a bad sample to abort a drive-long recording).
    func append<T: Encodable>(_ record: T) {
        queue.async { [weak self] in
            guard let self else { return }
            do {
                var line = try self.encoder.encode(record)
                line.append(0x0A) // '\n'
                self.buffer.append(line)
                self.count += 1
                if self.buffer.count >= self.flushThreshold {
                    self.flushLocked()
                }
            } catch {
                self.logger.error("Failed to encode record: \(error.localizedDescription)")
            }
        }
    }

    /// Thread-safe row count for the manifest / live UI.
    var rowCount: Int {
        queue.sync { count }
    }

    /// Flush remaining buffer and close the file. Blocks until done.
    func close() {
        queue.sync {
            flushLocked()
            do {
                try handle.close()
            } catch {
                logger.error("Failed to close \(self.url.lastPathComponent): \(error.localizedDescription)")
            }
        }
    }

    /// Must be called on `queue`.
    private func flushLocked() {
        guard !buffer.isEmpty else { return }
        do {
            try handle.write(contentsOf: buffer)
            buffer.removeAll(keepingCapacity: true)
        } catch {
            logger.error("Failed to write to \(self.url.lastPathComponent): \(error.localizedDescription)")
        }
    }
}
