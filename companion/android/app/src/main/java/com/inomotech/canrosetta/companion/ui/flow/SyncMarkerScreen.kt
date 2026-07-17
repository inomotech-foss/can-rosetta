package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * Sync marker: flashing the brakes N times gives one shared instant visible in
 * video + CAN + IMU, which the server cross-correlates to pin the clocks. The
 * phone can flag the IMU deceleration locally; the CAN/video offsets are computed
 * server-side.
 */
@Composable
fun SyncMarkerScreen(
    markerPinned: Boolean,
    onPin: () -> Unit,
    onContinue: () -> Unit,
    onSkip: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Sync marker", color = FlowTheme.text, fontSize = 24.sp, fontWeight = FontWeight.Bold)

        FlowCard(padding = PaddingValues(vertical = 22.dp, horizontal = 18.dp)) {
            Column(
                Modifier.fillMaxWidth(),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                Text("3×", color = FlowTheme.text, fontSize = 56.sp, fontWeight = FontWeight.Black)
                Text("Flash the brakes", color = FlowTheme.text, fontSize = 17.sp,
                     fontWeight = FontWeight.SemiBold)
                Text(
                    "While stationary, before you pull out. Firm, distinct pulses.",
                    color = FlowTheme.textSecondary, fontSize = 13.sp, textAlign = TextAlign.Center,
                )
            }
        }

        FlowCard {
            markerRow("Video · lamp pixels", markerPinned)
            RowSeparator(leadingInset = 54)
            markerRow("CAN · brake bit", markerPinned)
            RowSeparator(leadingInset = 54)
            markerRow("IMU · decel spikes", markerPinned, localOk = true)
        }

        Text(
            "Three short flashes — one shared instant in three streams; the server " +
                "cross-correlates them to pin the clocks.",
            color = FlowTheme.textMuted, fontSize = 12.sp,
        )

        Spacer(Modifier.height(2.dp))
        if (!markerPinned) {
            PrimaryButton("Pin marker & continue", background = FlowTheme.greenFill,
                          textColor = FlowTheme.green) { onPin(); onContinue() }
        } else {
            PrimaryButton("Continue", background = FlowTheme.greenFill, textColor = FlowTheme.green) {
                onContinue()
            }
        }
        SecondaryButton("Skip") { onSkip() }
    }
}

@Composable
private fun markerRow(label: String, pinned: Boolean, localOk: Boolean = false) {
    val ok = pinned && localOk
    val detail = when {
        !pinned -> "waiting"
        localOk -> "pinned locally"
        else -> "pending · server aligns"
    }
    CheckRow(ok = ok, label = label, detail = detail)
}
