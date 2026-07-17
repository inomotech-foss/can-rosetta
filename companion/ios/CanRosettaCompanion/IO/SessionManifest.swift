import Foundation
import UIKit
import os

/// Codable model of `manifest.json`, matching `schemas/manifest.schema.json`.
///
/// Property names are idiomatic camelCase; the encoder is configured with
/// `.convertToSnakeCase`, so `schemaVersion` -> `schema_version`,
/// `createdUtc` -> `created_utc`, `utcOffsetEstS` -> `utc_offset_est_s`, etc.
struct Manifest: Codable {
    var schemaVersion: String
    var sessionId: String
    var createdUtc: Double
    var vehicle: Vehicle?
    var devices: [Device]
    var streams: [Stream]
    var syncMarkers: [SyncMarker]?

    struct Vehicle: Codable {
        var make: String?
        var model: String?
        var year: Int?
        var vinHash: String?
        var notes: String?
    }

    struct Device: Codable {
        var role: String        // "edge" | "companion"
        var kind: String        // "ios"
        var id: String
        var swVersion: String?
        var mount: String?
        var clock: Clock?

        struct Clock: Codable {
            var source: String  // "ntp" | "gps" | "manual" | "unknown"
            var utcOffsetEstS: Double?
            var errEstS: Double?
        }
    }

    struct Stream: Codable {
        var path: String
        var kind: String        // "motion" | "location" | "video" | ...
        var rows: Int?
        var index: String?
        var tStartUtc: Double?
        var tEndUtc: Double?
    }

    struct SyncMarker: Codable {
        var kind: String
        var tUtc: Double
        var count: Int?
    }
}

enum ManifestVersion {
    static let schemaVersion = "1.0.0"
    static let softwareVersion = "can-rosetta-companion/0.1.0"
}

extension Manifest {

    /// Build the companion's manifest for a finished session.
    static func make(
        sessionId: String,
        createdUtc: Double,
        clock: Manifest.Device.Clock,
        streams: [Manifest.Stream],
        mount: String? = nil,
        syncMarkers: [Manifest.SyncMarker]? = nil
    ) -> Manifest {
        let device = Manifest.Device(
            role: "companion",
            kind: "ios",
            id: Manifest.deviceID(),
            swVersion: ManifestVersion.softwareVersion,
            mount: mount,
            clock: clock
        )
        return Manifest(
            schemaVersion: ManifestVersion.schemaVersion,
            sessionId: sessionId,
            createdUtc: createdUtc,
            vehicle: nil,
            devices: [device],
            streams: streams,
            syncMarkers: (syncMarkers?.isEmpty ?? true) ? nil : syncMarkers
        )
    }

    /// A stable, non-PII device identifier for `devices[].id`.
    /// `identifierForVendor` is per-vendor and resets on uninstall — good
    /// enough to distinguish phones in a merge, without being a hardware serial.
    static func deviceID() -> String {
        let raw = UIDevice.current.identifierForVendor?.uuidString ?? UUID().uuidString
        return "iphone-" + raw.prefix(8).lowercased()
    }

    /// Serialise to `manifest.json` at `url`.
    func write(to url: URL) throws {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        let data = try encoder.encode(self)
        try data.write(to: url, options: .atomic)
    }
}
