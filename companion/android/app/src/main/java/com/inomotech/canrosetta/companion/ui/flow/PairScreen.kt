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
import com.inomotech.canrosetta.companion.remote.JoinStatus
import com.inomotech.canrosetta.companion.remote.WifiJoinState
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
    val wifiSsid by connection.wifiSsid.collectAsState()
    val wifiPsk by connection.wifiPsk.collectAsState()
    val wifiJoin by connection.wifiJoin.collectAsState()
    val sessionId by controller.sessionId.collectAsState()
    var scanning by remember { mutableStateOf(false) }

    val connected = state.conn == ConnState.CONNECTED
    val hasWifi = wifiSsid.isNotBlank() && wifiPsk.isNotBlank()
    val wifiJoined = wifiJoin.status == JoinStatus.JOINED

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
                    // Joins the AP first when the payload carried credentials;
                    // degrades to a plain pair() when it did not.
                    connection.joinAndPair()
                }
            }
            SecondaryButton("Cancel scan") { scanning = false }
        } else {
            SecondaryButton("Scan QR") { scanning = true }
        }

        // AutoPi Wi-Fi — programmatic AP join from the v2 QR credentials.
        FlowCard(padding = androidx.compose.foundation.layout.PaddingValues(16.dp)) {
            SectionLabel("AutoPi Wi-Fi")
            Spacer(Modifier.height(8.dp))
            Text(
                if (hasWifi) wifiSsid else "not provisioned",
                color = if (hasWifi) FlowTheme.text else FlowTheme.textMuted,
                fontSize = 15.sp, fontFamily = Mono,
            )
            Spacer(Modifier.height(12.dp))
            // Re-connect semantics when already joined: re-filing the identical
            // specifier request is cheap and auto-approved by the OS.
            PrimaryButton(
                if (wifiJoined) "Wi-Fi joined — tap to re-connect" else "Connect to AutoPi Wi-Fi",
                enabled = hasWifi,
            ) { connection.connectWifi() }
            Spacer(Modifier.height(8.dp))
            Text(
                wifiLine(wifiJoin, hasWifi),
                color = if (wifiJoined) FlowTheme.green else FlowTheme.textMuted,
                fontSize = 12.sp, fontFamily = Mono,
            )
            if (wifiJoin.status == JoinStatus.UNSUPPORTED) {
                Spacer(Modifier.height(4.dp))
                Text(
                    "Android 9 or older — join the AutoPi Wi-Fi in Settings, then Pair",
                    color = FlowTheme.amber, fontSize = 12.sp,
                )
            }
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

/** One-line join status for the Wi-Fi card, mirroring [handshakeLine]. */
private fun wifiLine(join: WifiJoinState, hasWifi: Boolean): String = when (join.status) {
    JoinStatus.IDLE -> join.message ?: if (hasWifi) "not joined" else "scan a QR with Wi-Fi credentials"
    JoinStatus.REQUESTING -> "requesting network — approve the system dialog"
    JoinStatus.JOINED -> "joined · control traffic bound to AutoPi"
    JoinStatus.FAILED -> join.message ?: "join failed"
    JoinStatus.UNSUPPORTED -> "programmatic join unavailable"
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
        // v2 payloads optionally carry the AP credentials. The QR is the source
        // of truth: a payload without them (v1, dev boxes) must also CLEAR any
        // previously persisted pair — leftover credentials would join a
        // different AutoPi's AP. Mirrors the iOS `payload.wifi?.ssid ?? ""`.
        val wifi = obj.optJSONObject("wifi")
        connection.setWifiSsid(wifi?.optString("ssid").orEmpty())
        connection.setWifiPsk(wifi?.optString("psk").orEmpty())
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
