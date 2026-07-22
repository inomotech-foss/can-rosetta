import CarPlay

/// The CAN-Rosetta CarPlay "car app": a driving-task template surface on the
/// head unit that mirrors the recording-status widget and drives the **same**
/// `RecordingController` as the phone UI and the widget/Live Activity — no
/// forked logic. It reads the published `RecordingSnapshot` for state and
/// invokes the shared Stop / Pin-marker actions through `RecordingWidgetActions`.
///
/// Starting a drive stays phone-side on purpose: start needs the pre-flight flow
/// (permissions, mount check, pairing), which CarPlay templates cannot present —
/// the same reason the idle widget deep-links instead of offering a Start button.
///
/// Gated by the `com.apple.developer.carplay-driving-task` entitlement, which is
/// a *managed* capability Apple grants per app. Until that grant lands it is
/// applied to Simulator builds only (see project.yml), so device/TestFlight
/// signing keeps working and simply never connects a CarPlay scene.
@MainActor
final class CarPlaySceneDelegate: UIResponder, CPTemplateApplicationSceneDelegate {
    private var interfaceController: CPInterfaceController?
    private var listTemplate: CPListTemplate?
    private var refreshTimer: Timer?

    func templateApplicationScene(_ templateApplicationScene: CPTemplateApplicationScene,
                                  didConnect interfaceController: CPInterfaceController) {
        self.interfaceController = interfaceController
        let template = CPListTemplate(title: "CAN-Rosetta", sections: sections())
        listTemplate = template
        interfaceController.setRootTemplate(template, animated: false, completion: nil)
        startRefreshing()
    }

    func templateApplicationScene(_ templateApplicationScene: CPTemplateApplicationScene,
                                  didDisconnectInterfaceController interfaceController: CPInterfaceController) {
        stopRefreshing()
        listTemplate = nil
        self.interfaceController = nil
    }

    // MARK: - Live refresh

    /// Rebuild the list from the latest snapshot ~once a second while the head
    /// unit is connected. `updateSections` mutates the live template in place
    /// (no root swap, no flicker); elapsed advances a second at a time, the right
    /// cadence for a glanceable head-unit surface. A target/action timer is used
    /// (not a block) so the callback lands on the main actor without an
    /// iOS-17-only `assumeIsolated` hop — the app floors at iOS 16.
    private func startRefreshing() {
        refresh()
        refreshTimer = Timer.scheduledTimer(
            timeInterval: 1, target: self, selector: #selector(refreshTick),
            userInfo: nil, repeats: true)
    }

    private func stopRefreshing() {
        refreshTimer?.invalidate()
        refreshTimer = nil
    }

    @objc private func refreshTick() {
        refresh()
    }

    private func refresh() {
        listTemplate?.updateSections(sections())
    }

    // MARK: - Templates

    private func sections() -> [CPListSection] {
        let snapshot = RecordingSnapshot.load()
        let recording = snapshot?.isRecording ?? false

        let status = CPListItem(
            text: recording ? "Recording drive" : "Not recording",
            detailText: recording ? elapsed(snapshot) : "Start a drive on your phone")
        var statusItems = [status]
        if recording {
            statusItems.append(CPListItem(text: "Samples", detailText: counts(snapshot)))
        }
        var result = [CPListSection(items: statusItems, header: "Status", sectionIndexTitle: nil)]

        if recording {
            let stop = CPListItem(text: "Stop recording", detailText: nil)
            stop.handler = { [weak self] _, completion in
                RecordingWidgetActions.shared.stopRecording?()
                completion()
                self?.refresh()
            }
            let marker = CPListItem(text: "Pin sync marker", detailText: "Triple brake-flash, then tap")
            marker.handler = { _, completion in
                RecordingWidgetActions.shared.pinSyncMarker?()
                completion()
            }
            result.append(CPListSection(items: [stop, marker], header: "Actions", sectionIndexTitle: nil))
        }
        return result
    }

    private func elapsed(_ snapshot: RecordingSnapshot?) -> String {
        guard let started = snapshot?.startedAtUTC else { return "—" }
        return Fmt.hms(Date().timeIntervalSince1970 - started)
    }

    private func counts(_ snapshot: RecordingSnapshot?) -> String {
        guard let snapshot else { return "—" }
        var line = "\(snapshot.motionCount) IMU · \(snapshot.locationCount) GPS"
        if let accuracy = snapshot.gpsAccuracyM {
            line += String(format: " ±%.0f m", accuracy)
        }
        return line
    }
}
