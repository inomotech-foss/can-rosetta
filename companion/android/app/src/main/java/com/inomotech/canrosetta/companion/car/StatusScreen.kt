package com.inomotech.canrosetta.companion.car

import androidx.car.app.CarContext
import androidx.car.app.Screen
import androidx.car.app.model.Action
import androidx.car.app.model.Pane
import androidx.car.app.model.PaneTemplate
import androidx.car.app.model.ParkedOnlyOnClickListener
import androidx.car.app.model.Row
import androidx.car.app.model.Template
import androidx.lifecycle.lifecycleScope
import com.inomotech.canrosetta.companion.CanRosettaApplication
import com.inomotech.canrosetta.companion.recording.RecordingStatus
import com.inomotech.canrosetta.companion.remote.ConnState
import com.inomotech.canrosetta.companion.remote.EdgeUiState
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * The single car-screen surface: a live status pane with a Start/Stop toggle.
 *
 * The toggle drives the SAME coordinated path the phone UI uses (see
 * `DriveFlowViewModel.startRecording`): edge-first via
 * `EdgeConnection.start/stopRecording(controller)` when an AutoPi is
 * configured AND connected, plain `controller.start()/stop()` otherwise — so
 * a car-screen start never behaves differently from a phone start.
 */
class StatusScreen(
    carContext: CarContext,
    private val app: CanRosettaApplication,
    private val logger: CarHardwareLogger,
) : Screen(carContext) {

    private val controller = app.recordingController
    private val connection = app.edgeConnection

    init {
        // Re-render on any state change, throttled to ~1 Hz: conflate keeps only
        // the latest change while the collector sleeps, and the host throttles
        // template refreshes anyway, so faster invalidation buys nothing.
        lifecycleScope.launch {
            combine(
                controller.status,
                connection.state,
                logger.summary,
                app.foregroundServiceBlocked,
            ) { _, _, _, _ -> }
                .conflate()
                .collect {
                    invalidate()
                    delay(1000)
                }
        }
    }

    override fun onGetTemplate(): Template {
        val rec = controller.status.value
        val edge = connection.state.value

        val pane = Pane.Builder()
            .addRow(statusRow(rec))
            .addRow(sensorsRow(rec))
            .addRow(edgeRow(edge))
            .addRow(carDataRow())
            .addAction(toggleAction(rec.isRecording))
            .addAction(grantCarDataAction())
            .build()

        return PaneTemplate.Builder(pane)
            .setHeaderAction(Action.APP_ICON)
            .setTitle("CAN-Rosetta")
            .build()
    }

    // MARK: - Rows

    private fun statusRow(rec: RecordingStatus): Row {
        val builder = Row.Builder()
            .setTitle(if (rec.isRecording) "Recording — ${formatElapsed(rec.elapsed)}" else "Idle")
            .addText("Session ${controller.sessionId.value.take(8)}")
        if (rec.isRecording && app.foregroundServiceBlocked.value) {
            // The OS refused the background foreground-service start (see
            // RecordingForegroundService); recording runs but is killable.
            builder.addText("Open the phone app once to keep recording while locked")
        }
        return builder.build()
    }

    private fun sensorsRow(rec: RecordingStatus): Row = Row.Builder()
        .setTitle("Phone sensors")
        .addText(
            String.format(Locale.US, "IMU %.0f Hz · %,d rows", rec.imuRateHz, rec.motionCount)
        )
        .addText(
            buildString {
                append(
                    rec.gpsHorizontalAccuracy
                        ?.let { String.format(Locale.US, "GPS ±%.0f m", it) }
                        ?: "GPS no fix"
                )
                append(String.format(Locale.US, " · %,d fixes", rec.locationCount))
            }
        )
        .build()

    private fun edgeRow(edge: EdgeUiState): Row = Row.Builder()
        .setTitle("AutoPi")
        .addText(
            when {
                !connection.isConfigured() -> "Not configured"
                edge.conn == ConnState.CONNECTED ->
                    String.format(Locale.US, "%s · %,d frames", edge.edgeState, edge.frames)
                else -> "Not connected"
            }
        )
        .build()

    private fun carDataRow(): Row = Row.Builder()
        .setTitle("Car data")
        .addText(logger.summary.value)
        .build()

    // MARK: - Actions

    private fun toggleAction(isRecording: Boolean): Action = Action.Builder()
        .setTitle(if (isRecording) "Stop" else "Start")
        .setOnClickListener {
            toggleRecording(isRecording)
            invalidate() // reflect the toggle immediately, ahead of the 1 Hz loop
        }
        .build()

    /**
     * Same decision the phone drive flow makes: coordinate with the edge only
     * when it is configured and the health check has succeeded; otherwise an
     * honest phone-only recording.
     */
    private fun toggleRecording(isRecording: Boolean) {
        val coordinated = connection.isConfigured() && connection.isConnected()
        if (isRecording) {
            if (coordinated) connection.stopRecording(controller) else controller.stop()
        } else {
            // Singleton controller is reused across drives; mint a fresh session
            // id so this drive's files land in their own directory.
            controller.newSessionId()
            // Car surface = docked/pocketed phone; never power the camera for a
            // car-initiated session.
            if (coordinated) connection.startRecording(controller, enableCamera = false)
            else controller.start(enableCamera = false)
        }
    }

    /**
     * Parked-only: hosts suppress permission UI while driving, and the actual
     * grant dialog appears on the PHONE screen, so the driver must be parked
     * and holding the phone anyway.
     */
    private fun grantCarDataAction(): Action = Action.Builder()
        .setTitle("Car data access")
        .setOnClickListener(ParkedOnlyOnClickListener.create {
            carContext.requestPermissions(CarHardwareLogger.CAR_DATA_PERMISSIONS) { _, _ ->
                // Re-register regardless of outcome: partial grants go live,
                // fresh denials become fresh permission_denied records.
                logger.refreshRegistrations()
                invalidate()
            }
        })
        .build()

    // MARK: - Formatting

    private fun formatElapsed(seconds: Double): String {
        val total = seconds.toLong().coerceAtLeast(0)
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        return if (h > 0) {
            String.format(Locale.US, "%d:%02d:%02d", h, m, s)
        } else {
            String.format(Locale.US, "%02d:%02d", m, s)
        }
    }
}
