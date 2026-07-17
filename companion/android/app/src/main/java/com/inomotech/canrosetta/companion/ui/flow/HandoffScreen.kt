package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.ui.shareSessionZip
import java.io.File

@Composable
fun HandoffScreen(
    controller: RecordingController,
    onAnotherDrive: () -> Unit,
) {
    val status by controller.status.collectAsState()
    val film by controller.filmDashboard.collectAsState()
    val context = LocalContext.current
    val archive = status.exportPath?.let { Fmt.fileSize(File(it)) }

    Column(
        Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(horizontal = 16.dp, vertical = 16.dp),
        verticalArrangement = Arrangement.spacedBy(14.dp),
    ) {
        Text("Session complete", color = FlowTheme.text, fontSize = 24.sp, fontWeight = FontWeight.Bold)

        FlowCard {
            InfoRow("Drive", "${Fmt.hms(status.elapsed)} · ${Fmt.distance(status.distanceMeters)}")
            RowSeparator()
            InfoRow("Motion", "${status.motionCount} samples")
            RowSeparator()
            InfoRow("Location", "${status.locationCount} fixes")
            if (film) {
                RowSeparator()
                InfoRow("Video + index", "${status.videoFrameCount} frames")
            }
            RowSeparator()
            InfoRow("Archive", archive ?: "packaging…")
        }

        Text(
            "The AutoPi uploads its part on its own — the server merges anything sharing " +
                "this session id.",
            color = FlowTheme.textMuted, fontSize = 12.sp,
        )
        Text(
            "So long, and thanks for all the frames.",
            color = FlowTheme.textMuted, fontSize = 12.sp,
            textAlign = TextAlign.Center, modifier = Modifier.fillMaxWidth(),
        )

        Spacer(Modifier.height(4.dp))
        PrimaryButton("Share archive", enabled = status.exportPath != null) {
            status.exportPath?.let { shareSessionZip(context, it) }
        }
        SecondaryButton("Record another drive") { onAnotherDrive() }
    }
}
