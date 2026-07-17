package com.inomotech.canrosetta.companion.ui.flow

import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.EdgeConnection
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * Navigation/state holder for the five-screen companion drive flow:
 * **Pair → Pre-flight → Recording → Sync marker → Hand-off**.
 *
 * It owns only navigation state; all recording/remote logic stays in
 * [RecordingController] and [EdgeConnection], which it calls through. Mirrors the
 * iOS `DriveFlowModel`.
 *
 * A plain state holder (not an AndroidX `ViewModel`) so it can take the shared
 * controller/connection in its constructor without a factory; the owning
 * `Activity` keeps it alive across recompositions.
 */
class DriveFlowViewModel(
    private val controller: RecordingController,
    private val connection: EdgeConnection,
) {
    enum class Phase { PAIR, PREFLIGHT, RECORDING, SYNC_MARKER, HANDOFF }

    /**
     * Whether this drive is coordinated with an AutoPi ([Mode.PAIRED]) or a
     * phone-only recording with no edge in the loop ([Mode.STANDALONE]). Only in
     * [Mode.PAIRED] is [EdgeConnection] ever touched.
     */
    enum class Mode { PAIRED, STANDALONE }

    private val _phase = MutableStateFlow(Phase.PAIR)
    val phase: StateFlow<Phase> = _phase

    private val _mode = MutableStateFlow(Mode.PAIRED)
    val mode: StateFlow<Mode> = _mode

    /** Set once the driver pins a sync marker for this drive. */
    private val _markerPinned = MutableStateFlow(false)
    val markerPinned: StateFlow<Boolean> = _markerPinned

    // MARK: - Transitions

    /**
     * Enter the flow paired with an AutoPi: warm up the pre-flight monitors, run
     * an edge health check if configured-but-not-connected, and step into
     * pre-flight.
     */
    fun confirmPaired() {
        _mode.value = Mode.PAIRED
        controller.startPreflight()
        if (connection.isConfigured() && !connection.isConnected()) {
            connection.checkHealth()
        }
        _phase.value = Phase.PREFLIGHT
    }

    /** Enter the flow phone-only — no pairing, no [EdgeConnection] involvement. */
    fun recordStandalone() {
        _mode.value = Mode.STANDALONE
        controller.startPreflight()
        _phase.value = Phase.PREFLIGHT
    }

    /**
     * Coordinated start when paired+connected, otherwise a phone-only recording.
     * In [Mode.STANDALONE] the [EdgeConnection] is never touched. Advances to the
     * recording screen, which reflects the live [RecordingController] state.
     */
    fun startRecording() {
        controller.stopPreflight()
        when (_mode.value) {
            Mode.STANDALONE -> controller.start()
            Mode.PAIRED ->
                if (connection.isConnected()) {
                    connection.startRecording(controller)
                } else {
                    // Honest fallback so the flow still works without an AutoPi in reach.
                    controller.start()
                    connection.connect()
                }
        }
        _phase.value = Phase.RECORDING
    }

    fun stopRecording() {
        when (_mode.value) {
            Mode.STANDALONE -> controller.stop()
            Mode.PAIRED ->
                if (connection.isConnected()) {
                    connection.stopRecording(controller)
                } else {
                    controller.stop()
                }
        }
        _phase.value = if (_markerPinned.value) Phase.HANDOFF else Phase.SYNC_MARKER
    }

    fun pinMarker() {
        if (_markerPinned.value) return
        controller.addSyncMarker(kind = "brake_pulse", count = 3)
        _markerPinned.value = true
    }

    fun continueToHandoff() { _phase.value = Phase.HANDOFF }

    fun skipMarker() { _phase.value = Phase.HANDOFF }

    /** Reset for a fresh drive (from the hand-off screen). */
    fun startAnotherDrive() {
        controller.newSessionId()
        _markerPinned.value = false
        _mode.value = Mode.PAIRED
        _phase.value = Phase.PAIR
    }
}
