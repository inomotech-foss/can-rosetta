package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.ConnState
import com.inomotech.canrosetta.companion.remote.EdgeConnection

private data class Check(val ok: Boolean, val label: String, val detail: String)

@Composable
fun PreflightScreen(
    controller: RecordingController,
    connection: EdgeConnection,
    standalone: Boolean,
    onStart: () -> Unit,
) {
    val status by controller.status.collectAsState()
    val mount by controller.mount.collectAsState()
    val edge by connection.state.collectAsState()
    val film by controller.filmDashboard.collectAsState()

    val storageOk = controller.freeDiskBytes() > 1_000_000_000L
    val motionOk = controller.isMotionAvailable

    val checks = buildList {
        if (!standalone) {
            val paired = edge.conn == ConnState.CONNECTED
            add(Check(paired, "Paired with AutoPi", edge.swVersion ?: (if (paired) "connected" else "not paired")))
            val pinned = edge.timeOffset != null
            add(Check(pinned, "Clocks pinned",
                      edge.timeOffset?.let { "%+.0f ms · Cristian".format(it * 1000) } ?: "not synced"))
        }
        add(Check(status.gpsHorizontalAccuracy != null, "GPS fix",
                  status.gpsHorizontalAccuracy?.let { "±%.0f m".format(it) } ?: "acquiring…"))
        add(Check(motionOk, "Motion", if (motionOk) "available" else "no IMU"))
        add(Check(storageOk, "Storage", Fmt.gbFree(controller.freeDiskBytes())))
        if (film) {
            add(Check(true, "Camera sees the dash", "ready"))
        }
        add(Check(mount.steady, "Phone mounted",
                  if (mount.steady) "steady" else "vibration high — snug the cradle"))
    }

    val startEnabled = storageOk && motionOk && mount.steady

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Pre-flight", color = FlowTheme.text, fontSize = 24.sp, fontWeight = FontWeight.Bold)

        Column(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(FlowTheme.cardRadius))
                .background(FlowTheme.indigoSubtleFill)
                .border(1.dp, FlowTheme.indigoSubtleBorder, RoundedCornerShape(FlowTheme.cardRadius))
                .padding(18.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("DON'T PANIC", color = FlowTheme.indigoLight, fontSize = 28.sp,
                 fontWeight = FontWeight.Black, letterSpacing = 1.5.sp)
            Text(
                if (standalone) {
                    "Phone-only drive. A few checks, then just drive normally."
                } else {
                    "A few checks, then just drive normally; the improbable part is the server's job."
                },
                color = FlowTheme.textSecondary, fontSize = 13.sp,
            )
        }

        FlowCard {
            checks.forEachIndexed { i, c ->
                CheckRow(c.ok, c.label, c.detail)
                if (i < checks.lastIndex) RowSeparator(leadingInset = 54)
            }
        }

        PrimaryButton("Start recording", enabled = startEnabled) { onStart() }
        if (!startEnabled) {
            Text("Enables itself once the cradle stops rattling.",
                 color = FlowTheme.textMuted, fontSize = 12.sp,
                 modifier = Modifier.fillMaxWidth().padding(top = 2.dp))
        }
    }
}
