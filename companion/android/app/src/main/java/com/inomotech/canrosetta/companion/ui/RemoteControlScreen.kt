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
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.Link
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Sync
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.ui.unit.dp
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.ConnState
import com.inomotech.canrosetta.companion.remote.EdgeConnection
import com.inomotech.canrosetta.companion.remote.EdgeMode

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RemoteControlScreen(
    controller: RecordingController,
    connection: EdgeConnection,
    onBack: () -> Unit,
) {
    val state by connection.state.collectAsState()
    val host by connection.host.collectAsState()
    val token by connection.token.collectAsState()
    val mode by connection.mode.collectAsState()
    val recStatus by controller.status.collectAsState()
    val sessionId by controller.sessionId.collectAsState()

    Scaffold(
        topBar = {
            TopAppBar(
                title = { Text("Remote AutoPi") },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding)
                .verticalScroll(rememberScrollState()),
        ) {
            SectionCard("AutoPi") {
                OutlinedTextField(
                    value = host,
                    onValueChange = { connection.setHost(it) },
                    label = { Text("http://192.168.4.1:8765") },
                    enabled = !recStatus.isRecording,
                    singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                    textStyle = MaterialTheme.typography.bodyMedium.copy(fontFamily = FontFamily.Monospace),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(
                    value = token,
                    onValueChange = { connection.setToken(it) },
                    label = { Text("Bearer token") },
                    enabled = !recStatus.isRecording,
                    singleLine = true,
                    visualTransformation = PasswordVisualTransformation(),
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                )
                OutlinedButton(
                    onClick = { connection.checkHealth() },
                    enabled = !state.isBusy,
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                ) {
                    Icon(Icons.Filled.Link, contentDescription = null)
                    Text("  Connect")
                }
                StatusRow("Status", connectionStatusText(state.conn, state.connMessage, state.swVersion))
            }

            SectionCard("Time sync") {
                OutlinedButton(
                    onClick = { connection.syncTime() },
                    enabled = !state.isBusy && state.conn == ConnState.CONNECTED,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Filled.Sync, contentDescription = null)
                    Text("  Sync clocks")
                }
                state.timeOffset?.let {
                    StatusRow("Edge − phone offset", "%+.1f ms".format(it * 1000))
                }
                state.timeRoundTrip?.let {
                    StatusRow("Round-trip", "%.1f ms".format(it * 1000))
                }
            }

            SectionCard("Investigation") {
                Row(
                    modifier = Modifier.fillMaxWidth().padding(bottom = 8.dp),
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    EdgeMode.entries.forEach { m ->
                        FilterChip(
                            selected = mode == m,
                            onClick = { connection.setMode(m) },
                            enabled = !recStatus.isRecording,
                            label = { Text(m.label) },
                        )
                    }
                }
                OutlinedButton(
                    onClick = { connection.discover(sessionId) },
                    enabled = !state.isBusy && state.conn == ConnState.CONNECTED,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Icon(Icons.Filled.Search, contentDescription = null)
                    Text("  Discover")
                }
                state.discoverySummary?.let { s ->
                    StatusRow("OBD PIDs", (s.obdPids ?: 0).toString())
                    StatusRow("UDS DIDs", (s.udsDids ?: 0).toString())
                    StatusRow("Plain CAN IDs", (s.plainCanIds ?: 0).toString())
                }
            }

            SectionCard("Coordinated recording") {
                StatusRow("Session ID", sessionId)
                Button(
                    onClick = {
                        if (recStatus.isRecording) {
                            connection.stopRecording(controller)
                        } else {
                            connection.startRecording(controller)
                        }
                    },
                    enabled = !state.isBusy,
                    colors = if (recStatus.isRecording) {
                        ButtonDefaults.buttonColors(containerColor = Color(0xFFB00020))
                    } else {
                        ButtonDefaults.buttonColors()
                    },
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                ) {
                    Text(if (recStatus.isRecording) "Stop recording" else "Start recording")
                }
                Text(
                    "Starts the phone recording and the AutoPi log together, using the same session id.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }

            SectionCard("Edge status") {
                StatusRow("State", state.edgeState)
                StatusRow("Frames", state.frames.toString())
                StatusRow("OBD samples", state.obdSamples.toString())
                StatusRow("Elapsed", "%.0f s".format(state.elapsed))
                StatusRow("Live feed", if (state.wsConnected) "WebSocket" else "Polling")
            }

            state.lastError?.let {
                SectionCard("Error") {
                    Text(
                        it,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.error,
                    )
                }
            }
        }
    }
}

private fun connectionStatusText(conn: ConnState, message: String?, swVersion: String?): String =
    when (conn) {
        ConnState.IDLE -> "Not connected"
        ConnState.CONNECTING -> "Connecting…"
        ConnState.CONNECTED -> swVersion?.let { "Connected · $it" } ?: "Connected"
        ConnState.FAILED -> message ?: "Failed"
    }
