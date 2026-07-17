package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.EdgeConnection

/** Hosts the five-screen drive flow, switching on [DriveFlowViewModel.phase]. */
@Composable
fun DriveFlowScreen(
    controller: RecordingController,
    connection: EdgeConnection,
    vm: DriveFlowViewModel,
    onOpenAdvanced: () -> Unit,
) {
    val phase by vm.phase.collectAsState()
    val mode by vm.mode.collectAsState()
    val markerPinned by vm.markerPinned.collectAsState()
    val standalone = mode == DriveFlowViewModel.Mode.STANDALONE

    Box(Modifier.fillMaxSize().background(FlowTheme.pageBg)) {
        when (phase) {
            DriveFlowViewModel.Phase.PAIR ->
                PairScreen(
                    controller, connection,
                    onConfirmPaired = { vm.confirmPaired() },
                    onRecordStandalone = { vm.recordStandalone() },
                    onOpenAdvanced = onOpenAdvanced,
                )
            DriveFlowViewModel.Phase.PREFLIGHT ->
                PreflightScreen(controller, connection, standalone = standalone,
                                onStart = { vm.startRecording() })
            DriveFlowViewModel.Phase.RECORDING ->
                RecordingScreen(controller, connection, standalone = standalone,
                                onStop = { vm.stopRecording() })
            DriveFlowViewModel.Phase.SYNC_MARKER ->
                SyncMarkerScreen(
                    markerPinned = markerPinned,
                    onPin = { vm.pinMarker() },
                    onContinue = { vm.continueToHandoff() },
                    onSkip = { vm.skipMarker() },
                )
            DriveFlowViewModel.Phase.HANDOFF ->
                HandoffScreen(controller, onAnotherDrive = { vm.startAnotherDrive() })
        }
    }
}
