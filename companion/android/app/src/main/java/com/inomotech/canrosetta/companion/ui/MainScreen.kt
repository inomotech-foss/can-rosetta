package com.inomotech.canrosetta.companion.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Router
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.EdgeConnection

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainScreen(
    controller: RecordingController,
    connection: EdgeConnection,
    onOpenRemote: () -> Unit,
) {
    val status by controller.status.collectAsState()
    val sessionId by controller.sessionId.collectAsState()
    val filmDashboard by controller.filmDashboard.collectAsState()
    val capturePhotos by controller.capturePhotos.collectAsState()
    val context = LocalContext.current

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(if (status.isRecording) "Recording" else "CAN-Rosetta")
                },
                actions = {
                    IconButton(onClick = onOpenRemote) {
                        Icon(Icons.Filled.Router, contentDescription = "Remote AutoPi")
                    }
                },
            )
        },
        bottomBar = {
            Button(
                onClick = { if (status.isRecording) controller.stop() else controller.start() },
                enabled = sessionId.isNotEmpty(),
                colors = if (status.isRecording) {
                    ButtonDefaults.buttonColors(containerColor = Color(0xFFB00020))
                } else {
                    ButtonDefaults.buttonColors()
                },
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(16.dp),
            ) {
                Text(if (status.isRecording) "Stop recording" else "Start recording")
            }
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {
            SectionCard("Session") {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Session ID", style = MaterialTheme.typography.bodyMedium)
                    IconButton(
                        onClick = { controller.newSessionId() },
                        enabled = !status.isRecording,
                    ) {
                        Icon(Icons.Filled.Refresh, contentDescription = "New session id")
                    }
                }
                OutlinedTextField(
                    value = sessionId,
                    onValueChange = { controller.setSessionId(it) },
                    enabled = !status.isRecording,
                    singleLine = true,
                    modifier = Modifier.fillMaxWidth(),
                    textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                )
                Text(
                    "Must match the AutoPi's session id so the server can merge both parts.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            SectionCard("Live status") {
                StatusRow("Recording time", formatElapsed(status.elapsed))
                StatusRow("IMU rate", "%.0f Hz".format(status.imuRateHz))
                StatusRow("Motion samples", status.motionCount.toString())
                StatusRow("GPS fixes", status.locationCount.toString())
                StatusRow("GPS fix", gpsFixString(status.gpsHorizontalAccuracy, status.locationPermissionGranted))
                if (filmDashboard) StatusRow("Video frames", status.videoFrameCount.toString())
                if (capturePhotos) StatusRow("Stills", status.photoCount.toString())
            }

            SectionCard("Options") {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Film dashboard", style = MaterialTheme.typography.bodyMedium)
                    Switch(
                        checked = filmDashboard,
                        onCheckedChange = { controller.setFilmDashboard(it) },
                        enabled = !status.isRecording,
                    )
                }
                Row(
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text("Capture stills", style = MaterialTheme.typography.bodyMedium)
                    Switch(
                        checked = capturePhotos,
                        onCheckedChange = { controller.setCapturePhotos(it) },
                        enabled = !status.isRecording,
                    )
                }
                Text(
                    "Film records the rear camera to video.mp4 (dense, good for telltales/needles). " +
                        "Stills save periodic full-resolution photos (sharp, good for OCR). Both share one camera.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            SectionCard("Export") {
                val exportPath = status.exportPath
                if (exportPath != null) {
                    OutlinedButton(
                        onClick = { shareSessionZip(context, exportPath) },
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Icon(Icons.Filled.Share, contentDescription = null)
                        Text("  Share session archive")
                    }
                } else {
                    Text(
                        "Stop a recording to produce a shareable session archive.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                status.lastError?.let {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                        modifier = Modifier.padding(top = 8.dp),
                    )
                }
            }
        }
    }
}

private fun gpsFixString(accuracy: Double?, permissionGranted: Boolean): String = when {
    !permissionGranted -> "No permission"
    accuracy != null -> "±%.0f m".format(accuracy)
    else -> "Waiting…"
}
