import SwiftUI

/// 05 — Hand-off. The drive summary from real `RecordingController` counters,
/// an honest note about how the two halves reach the server, and the share
/// action for the exported archive.
struct HandoffView: View {
    @EnvironmentObject private var controller: RecordingController
    @EnvironmentObject private var flow: DriveFlowModel
    @State private var showShare = false

    private var archiveReady: Bool { controller.exportURL != nil }

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    summaryCard
                    uploadCard
                    Text("So long, and thanks for all the frames.")
                        .font(.system(.subheadline).italic())
                        .foregroundStyle(Theme.textSecondary)
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, 4)
                }
                .padding(.horizontal, 20)
                .padding(.top, 8)
                .padding(.bottom, 12)
            }
            VStack(spacing: 10) {
                PrimaryButton(title: archiveReady ? "Share archive" : "Preparing archive…",
                              enabled: archiveReady) { showShare = true }
                Button("Start another drive") { flow.startAnotherDrive(controller: controller) }
                    .font(.subheadline).foregroundStyle(Theme.textMuted)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 8)
        }
        .sheet(isPresented: $showShare) {
            if let url = controller.exportURL { ShareSheet(items: [url]) }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 4) {
            SectionLabel(text: "Step 5 of 5")
            Text("Hand-off")
                .font(.system(size: 28, weight: .bold))
                .foregroundStyle(Theme.text)
            Text("Drive captured. Here is your session part.")
                .font(.subheadline).foregroundStyle(Theme.textSecondary)
        }
    }

    private var summaryCard: some View {
        FlowCard(padding: 6) {
            VStack(spacing: 0) {
                row("Drive", driveValue)
                RowSeparator(leadingInset: 12)
                row("Motion", "\(controller.motionCount) samples")
                RowSeparator(leadingInset: 12)
                row("Location", "\(controller.locationCount) fixes")
                if controller.filmDashboard {
                    RowSeparator(leadingInset: 12)
                    row("Video + index", "\(controller.videoFrameCount) frames")
                }
                RowSeparator(leadingInset: 12)
                row("Archive", archiveValue)
            }
        }
    }

    private var uploadCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: "arrow.up.circle").foregroundStyle(Theme.indigoLight)
                Text("Uploading phone part").font(.system(.subheadline, weight: .semibold))
                    .foregroundStyle(Theme.text)
            }
            Text("The AutoPi uploads its CAN part on its own. Share this archive and the server merges anything carrying the same session id — there is no phone-to-server uploader.")
                .font(.caption).foregroundStyle(Theme.textSecondary)
            HStack(spacing: 6) {
                Text("session").font(.mono(.caption2)).foregroundStyle(Theme.textMuted)
                Text(controller.sessionId).font(.mono(.caption2)).foregroundStyle(Theme.indigoLight)
                    .lineLimit(1).truncationMode(.middle)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Theme.indigoSubtleFill, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
            .strokeBorder(Theme.indigoSubtleBorder, lineWidth: 1))
    }

    private func row(_ label: String, _ value: String) -> some View {
        InfoRow(label: label, value: value).padding(.horizontal, 12)
    }

    private var driveValue: String {
        let dur = Fmt.hms(controller.elapsed)
        if controller.distanceMeters > 0 {
            return "\(dur) · \(Fmt.distance(controller.distanceMeters))"
        }
        return dur
    }

    private var archiveValue: String {
        guard let url = controller.exportURL else { return "preparing…" }
        if let size = Fmt.fileSize(url) { return "\(size) · \(url.lastPathComponent)" }
        return url.lastPathComponent
    }
}

#Preview {
    HandoffView()
        .environmentObject(RecordingController())
        .environmentObject(DriveFlowModel())
        .preferredColorScheme(.dark)
}
