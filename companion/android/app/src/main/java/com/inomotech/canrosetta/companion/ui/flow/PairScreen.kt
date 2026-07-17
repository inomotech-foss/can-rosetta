package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.ConnState
import com.inomotech.canrosetta.companion.remote.EdgeConnection
import org.json.JSONObject

@Composable
fun PairScreen(
    controller: RecordingController,
    connection: EdgeConnection,
    onConfirmPaired: () -> Unit,
    onRecordStandalone: () -> Unit,
    onOpenAdvanced: () -> Unit,
) {
    val state by connection.state.collectAsState()
    val host by connection.host.collectAsState()
    val token by connection.token.collectAsState()
    val sessionId by controller.sessionId.collectAsState()
    var scanning by remember { mutableStateOf(false) }

    val connected = state.conn == ConnState.CONNECTED

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Pair AutoPi", color = FlowTheme.text, fontSize = 24.sp, fontWeight = FontWeight.Bold)
        Text(
            "Headless AutoPi? The installer prints the host + token (and a QR you can " +
                "scan from your SSH terminal). Scan it, or enter them below.",
            color = FlowTheme.textMuted, fontSize = 12.sp,
        )

        if (scanning) {
            Box(
                Modifier
                    .fillMaxWidth()
                    .height(240.dp)
                    .clip(RoundedCornerShape(FlowTheme.cardRadius)),
            ) {
                QrScanner(Modifier.fillMaxSize()) { payload ->
                    applyPayload(payload, controller, connection)
                    scanning = false
                    connection.pair()
                    connection.syncTime()
                }
            }
            SecondaryButton("Cancel scan") { scanning = false }
        } else {
            SecondaryButton("Scan QR") { scanning = true }
        }

        // Manual entry — first-class for a headless unit.
        FlowCard(padding = androidx.compose.foundation.layout.PaddingValues(16.dp)) {
            SectionLabel("Host + token")
            Spacer(Modifier.height(8.dp))
            DarkField("Host", host, KeyboardType.Uri) { connection.setHost(it) }
            Spacer(Modifier.height(8.dp))
            DarkField("Control token", token, KeyboardType.Text) { connection.setToken(it) }
            Spacer(Modifier.height(12.dp))
            PrimaryButton(if (connected) "Re-check" else "Pair") {
                connection.pair()
                connection.syncTime()
            }
            Spacer(Modifier.height(8.dp))
            Text(handshakeLine(state), color = if (connected) FlowTheme.green else FlowTheme.textMuted,
                 fontSize = 12.sp, fontFamily = Mono)
        }

        // Session details.
        FlowCard {
            InfoRow("Session", sessionId, FlowTheme.textSecondary)
            RowSeparator()
            InfoRow("Control token", if (connected) "verified" else "unverified",
                    if (connected) FlowTheme.green else FlowTheme.textMuted)
            RowSeparator()
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 16.dp).height(50.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text("Pairing phrase", color = FlowTheme.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
                Row(horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                    Chip("darmok"); Chip("jalad"); Chip("tanagra")
                }
            }
        }

        PrimaryButton("Confirm — arm both recorders", enabled = connected) { onConfirmPaired() }
        SecondaryButton("Record without AutoPi") { onRecordStandalone() }
        Text(
            "Advanced control",
            color = FlowTheme.indigoLight, fontSize = 13.sp, fontWeight = FontWeight.Medium,
            modifier = Modifier.padding(top = 2.dp).fillMaxWidth(),
        )
        SecondaryButton("Open advanced control") { onOpenAdvanced() }
    }
}

private fun handshakeLine(state: com.inomotech.canrosetta.companion.remote.EdgeUiState): String {
    if (state.conn != ConnState.CONNECTED) {
        return state.connMessage ?: "not paired"
    }
    val off = state.timeOffset
    val rtt = state.timeRoundTrip
    return if (off != null && rtt != null) {
        "handshake complete · offset %+.0f ms · rtt %.0f ms".format(off * 1000, rtt * 1000)
    } else {
        "handshake complete"
    }
}

private fun applyPayload(payload: String, controller: RecordingController, connection: EdgeConnection) {
    try {
        val obj = JSONObject(payload)
        obj.optString("host").takeIf { it.isNotEmpty() }?.let { connection.setHost(it) }
        obj.optString("token").takeIf { it.isNotEmpty() }?.let { connection.setToken(it) }
        obj.optString("session_id").takeIf { it.isNotEmpty() }?.let { controller.setSessionId(it) }
    } catch (_: Exception) {
        // Not a JSON pairing payload; ignore.
    }
}

@Composable
private fun DarkField(label: String, value: String, keyboard: KeyboardType, onChange: (String) -> Unit) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = keyboard),
        modifier = Modifier.fillMaxWidth(),
        colors = OutlinedTextFieldDefaults.colors(
            focusedTextColor = FlowTheme.text,
            unfocusedTextColor = FlowTheme.text,
            focusedBorderColor = FlowTheme.indigo,
            unfocusedBorderColor = FlowTheme.separator,
            focusedLabelColor = FlowTheme.textSecondary,
            unfocusedLabelColor = FlowTheme.textMuted,
            cursorColor = FlowTheme.indigo,
        ),
    )
}
