import SwiftUI

// MARK: - Card

/// A rounded card surface (`#1C1C1E`, radius 22).
struct FlowCard<Content: View>: View {
    var padding: CGFloat = 18
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 0) { content }
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(Theme.card, in: RoundedRectangle(cornerRadius: Theme.cardRadius, style: .continuous))
    }
}

/// Inset row separator, matching the design's left-inset hairline.
struct RowSeparator: View {
    var leadingInset: CGFloat = 0
    var body: some View {
        Theme.separator
            .frame(height: 1)
            .padding(.leading, leadingInset)
    }
}

// MARK: - Rows

/// A label / mono-value row used across the detail and stats cards.
struct InfoRow: View {
    let label: String
    let value: String
    var valueColor: Color = Theme.textSecondary
    var body: some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.system(.subheadline))
                .foregroundStyle(Theme.textSecondary)
            Spacer(minLength: 12)
            Text(value)
                .font(.mono(.subheadline))
                .foregroundStyle(valueColor)
                .multilineTextAlignment(.trailing)
        }
        .padding(.vertical, 11)
    }
}

/// Status of a pre-flight / sync check.
enum CheckStatus {
    case ok            // green check
    case warn          // amber blinking "!"
    case pending       // muted dot — server-side or not-yet-known

    var tint: Color {
        switch self {
        case .ok: return Theme.green
        case .warn: return Theme.amber
        case .pending: return Theme.textMuted
        }
    }
}

/// A checklist row: title + detail on the left, a status glyph on the right.
struct CheckRow: View {
    let title: String
    let detail: String
    let status: CheckStatus

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                    .font(.system(.subheadline, weight: .medium))
                    .foregroundStyle(Theme.text)
                Text(detail)
                    .font(.mono(.caption))
                    .foregroundStyle(Theme.textSecondary)
            }
            Spacer(minLength: 8)
            statusGlyph
        }
        .padding(.vertical, 11)
    }

    @ViewBuilder private var statusGlyph: some View {
        switch status {
        case .ok:
            ZStack {
                Circle().fill(Theme.greenFill)
                Image(systemName: "checkmark").font(.system(size: 12, weight: .bold))
                    .foregroundStyle(Theme.green)
            }
            .frame(width: 26, height: 26)
        case .warn:
            ZStack {
                Circle().fill(Theme.amberFill)
                Image(systemName: "exclamationmark").font(.system(size: 12, weight: .bold))
                    .foregroundStyle(Theme.amber)
            }
            .frame(width: 26, height: 26)
            .modifier(Blink())
        case .pending:
            ZStack {
                Circle().fill(Color.white.opacity(0.06))
                Circle().fill(Theme.textMuted).frame(width: 6, height: 6)
            }
            .frame(width: 26, height: 26)
        }
    }
}

// MARK: - Buttons

/// The primary indigo action button (radius 14, height 50, white 600).
struct PrimaryButton: View {
    let title: String
    var enabled: Bool = true
    var background: Color = Theme.indigo
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.white)
                .frame(maxWidth: .infinity)
                .frame(height: Theme.buttonHeight)
                .background(
                    (enabled ? background : background.opacity(0.35)),
                    in: RoundedRectangle(cornerRadius: Theme.buttonRadius, style: .continuous)
                )
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
        .animation(.easeInOut(duration: 0.25), value: enabled)
    }
}

// MARK: - Chips & pills

/// A small pill (indigo by default) — used for the pairing phrase words.
struct Chip: View {
    let text: String
    var fill: Color = Theme.indigoSubtleFill
    var border: Color = Theme.indigoSubtleBorder
    var fg: Color = Theme.indigoLight
    var body: some View {
        Text(text)
            .font(.mono(.caption))
            .foregroundStyle(fg)
            .padding(.horizontal, 12)
            .padding(.vertical, 7)
            .background(fill, in: Capsule())
            .overlay(Capsule().strokeBorder(border, lineWidth: 1))
    }
}

/// A status pill with a coloured dot + label (e.g. the REC pill, edge link).
struct StatusPill: View {
    let text: String
    var dotColor: Color = Theme.green
    var fill: Color = Theme.greenFill
    var fg: Color = Theme.green
    var blinkingDot: Bool = false
    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(dotColor).frame(width: 8, height: 8)
                .modifier(Blink(active: blinkingDot, duration: 1.2, minOpacity: 0.2))
            Text(text)
                .font(.system(.caption, weight: .semibold))
                .foregroundStyle(fg)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 7)
        .background(fill, in: Capsule())
    }
}

/// Section label above a card.
struct SectionLabel: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(.caption2, weight: .semibold))
            .tracking(1.1)
            .foregroundStyle(Theme.textMuted)
    }
}

// MARK: - Animations

/// A gentle opacity blink used for amber warnings and the REC dot.
struct Blink: ViewModifier {
    var active: Bool = true
    var duration: Double = 0.9
    var minOpacity: Double = 0.25
    @State private var on = false
    func body(content: Content) -> some View {
        content
            .opacity(active ? (on ? 1 : minOpacity) : 1)
            .onAppear {
                guard active else { return }
                withAnimation(.easeInOut(duration: duration).repeatForever(autoreverses: true)) {
                    on = true
                }
            }
    }
}

/// Green corner brackets overlay for the QR viewfinder.
struct CornerBrackets: View {
    var color: Color = Theme.green
    var length: CGFloat = 34
    var thickness: CGFloat = 3
    var inset: CGFloat = 14
    var body: some View {
        GeometryReader { geo in
            let w = geo.size.width, h = geo.size.height
            ZStack {
                bracket.position(x: inset + length / 2, y: inset + length / 2)             // TL
                bracket.rotationEffect(.degrees(90)).position(x: w - inset - length / 2, y: inset + length / 2)   // TR
                bracket.rotationEffect(.degrees(-90)).position(x: inset + length / 2, y: h - inset - length / 2)  // BL
                bracket.rotationEffect(.degrees(180)).position(x: w - inset - length / 2, y: h - inset - length / 2) // BR
            }
        }
    }
    private var bracket: some View {
        Path { p in
            p.move(to: CGPoint(x: 0, y: length))
            p.addLine(to: CGPoint(x: 0, y: 0))
            p.addLine(to: CGPoint(x: length, y: 0))
        }
        .stroke(color, style: StrokeStyle(lineWidth: thickness, lineCap: .round, lineJoin: .round))
        .frame(width: length, height: length)
    }
}
