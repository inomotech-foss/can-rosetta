import SwiftUI

/// b-on "Midnight" palette + shared metrics for the drive flow. All colours are
/// the design tokens translated to SwiftUI `Color`.
enum Theme {

    // MARK: Surfaces
    /// Page background `#030712`.
    static let pageBg = Color(hex: 0x030712)
    /// Card surface `#1C1C1E`.
    static let card = Color(hex: 0x1C1C1E)

    // MARK: Text
    static let text = Color.white
    static let textSecondary = Color.white.opacity(0.6)
    static let textMuted = Color.white.opacity(0.45)

    // MARK: Accents
    /// Primary indigo `#6366F1`.
    static let indigo = Color(hex: 0x6366F1)
    /// `#A5B4FC` — light indigo used for the "DON'T PANIC" title / chips text.
    static let indigoLight = Color(hex: 0xA5B4FC)
    /// `rgba(99,102,241,0.14)` — subtle indigo fill.
    static let indigoSubtleFill = Color(hex: 0x6366F1).opacity(0.14)
    /// `rgba(99,102,241,0.35)` — subtle indigo border.
    static let indigoSubtleBorder = Color(hex: 0x6366F1).opacity(0.35)

    static let green = Color(hex: 0x4ADE80)
    /// `rgba(34,197,94,0.16)`.
    static let greenFill = Color(hex: 0x22C55E).opacity(0.16)

    static let red = Color(hex: 0xEF4444)
    static let redLight = Color(hex: 0xF87171)
    /// `rgba(239,68,68,0.16)`.
    static let redFill = Color(hex: 0xEF4444).opacity(0.16)

    static let amber = Color(hex: 0xFBBF24)
    /// `rgba(245,158,11,0.16)`.
    static let amberFill = Color(hex: 0xF59E0B).opacity(0.16)

    /// Inset row separators `Color(white:0.33).opacity(0.65)`.
    static let separator = Color(white: 0.33).opacity(0.65)

    // MARK: Metrics
    static let cardRadius: CGFloat = 22
    static let buttonRadius: CGFloat = 14
    static let buttonHeight: CGFloat = 50
}

extension Color {
    /// Build a `Color` from a 24-bit `0xRRGGBB` literal.
    init(hex: UInt32) {
        let r = Double((hex >> 16) & 0xFF) / 255
        let g = Double((hex >> 8) & 0xFF) / 255
        let b = Double(hex & 0xFF) / 255
        self.init(.sRGB, red: r, green: g, blue: b, opacity: 1)
    }
}

extension Font {
    /// The monospaced face used for every "mono value" in the flow.
    static func mono(_ style: Font.TextStyle = .body) -> Font {
        .system(style, design: .monospaced)
    }
}

// MARK: - Formatting helpers

enum Fmt {
    static func hms(_ t: TimeInterval) -> String {
        let s = Int(max(0, t))
        return String(format: "%02d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }

    /// Human distance: metres under 1 km, else km with one decimal.
    static func distance(_ meters: Double) -> String {
        if meters < 1000 { return String(format: "%.0f m", meters) }
        return String(format: "%.1f km", meters / 1000)
    }

    static func gbFree(_ bytes: Int64) -> String {
        let gb = Double(bytes) / 1_000_000_000
        return String(format: "%.0f GB free", gb)
    }

    static func fileSize(_ url: URL) -> String? {
        guard let size = (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize
                ?? (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? nil
        else { return nil }
        let mb = Double(size) / 1_000_000
        if mb < 1 { return String(format: "%.0f KB", Double(size) / 1000) }
        return String(format: "%.1f MB", mb)
    }
}
