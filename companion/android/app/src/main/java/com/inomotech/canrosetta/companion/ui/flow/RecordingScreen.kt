package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateDpAsState
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.draw.shadow
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.ConnState
import com.inomotech.canrosetta.companion.remote.EdgeConnection

@Composable
fun RecordingScreen(
    controller: RecordingController,
    connection: EdgeConnection,
    standalone: Boolean,
    onStop: () -> Unit,
) {
    val status by controller.status.collectAsState()
    val accel by controller.accel.collectAsState()
    val edge by connection.state.collectAsState()
    val edgeUp = !standalone && edge.conn == ConnState.CONNECTED

    Column(Modifier.fillMaxSize().padding(horizontal = 16.dp, vertical = 16.dp)) {
        // top bar
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            StatusPill("REC", FlowTheme.red, FlowTheme.redFill, FlowTheme.redLight, blinkingDot = true)
            Spacer(Modifier.size(10.dp))
            Text(controller.sessionId.collectAsState().value.take(13) + "…",
                 color = FlowTheme.textSecondary, fontSize = 12.sp, fontFamily = Mono)
            Spacer(Modifier.weight(1f))
            Text(
                if (standalone) "phone only" else if (edgeUp) "edge link ✓" else "phone-only",
                color = if (standalone) FlowTheme.textMuted else if (edgeUp) FlowTheme.green else FlowTheme.amber,
                fontSize = 12.sp,
            )
        }

        Spacer(Modifier.height(24.dp))
        HalEye(accelX = accel.x, accelY = accel.y, modifier = Modifier.align(Alignment.CenterHorizontally))
        Spacer(Modifier.height(18.dp))
        Text(Fmt.hms(status.elapsed), color = FlowTheme.text, fontSize = 46.sp,
             fontWeight = FontWeight.SemiBold, fontFamily = FontFamily.Monospace,
             modifier = Modifier.align(Alignment.CenterHorizontally))
        Text(
            if (standalone) "recording locally — phone only"
            else if (edgeUp) "all systems fully operational" else "edge link down — recording locally",
            color = if (edgeUp || standalone) FlowTheme.textMuted else FlowTheme.amber,
            fontSize = 12.sp, letterSpacing = 0.5.sp,
            modifier = Modifier.align(Alignment.CenterHorizontally).padding(top = 4.dp),
        )

        Spacer(Modifier.height(20.dp))
        FlowCard {
            InfoRow("IMU", "%.0f Hz · %d samples".format(status.imuRateHz, status.motionCount))
            RowSeparator()
            InfoRow("GPS",
                    status.gpsHorizontalAccuracy?.let { "±%.0f m · %d fixes".format(it, status.locationCount) } ?: "—")
            val film by controller.filmDashboard.collectAsState()
            if (film) {
                RowSeparator()
                val fps = if (status.elapsed > 0) status.videoFrameCount / status.elapsed else 0.0
                InfoRow("Dashboard video", "%.0f fps · %d frames".format(fps, status.videoFrameCount))
            }
            if (!standalone) {
                RowSeparator()
                InfoRow("AutoPi · can0", if (edgeUp) "${edge.frames} frames · load —" else "—")
            }
        }

        Spacer(Modifier.weight(1f))
        PrimaryButton("Stop recording", background = FlowTheme.redFill, textColor = FlowTheme.redLight) {
            onStop()
        }
    }
}

/**
 * The HAL-9000 eye. The white highlight is a live **g-ball**: it rides the IMU's
 * user acceleration — centred at rest, sliding toward the acceleration direction
 * (right on lateral g, up on forward g), clamped to the rim; the eye breathes on
 * a red glow.
 */
@Composable
fun HalEye(accelX: Double, accelY: Double, modifier: Modifier = Modifier) {
    val infinite = rememberInfiniteTransition(label = "hal")
    val scale by infinite.animateFloat(
        1f, 1.06f, infiniteRepeatable(tween(1200), RepeatMode.Reverse), label = "halScale",
    )
    val glow by infinite.animateFloat(
        0.35f, 0.9f, infiniteRepeatable(tween(1200), RepeatMode.Reverse), label = "halGlow",
    )

    // Map acceleration (g) to a highlight offset, clamped inside the eye.
    val scalePtPerG = 60.0
    val maxR = 42.0
    var dx = accelX * scalePtPerG
    var dy = -accelY * scalePtPerG
    val mag = kotlin.math.hypot(dx, dy)
    if (mag > maxR && mag > 0) {
        dx = dx * maxR / mag
        dy = dy * maxR / mag
    }
    val animDx by animateDpAsState(dx.dp, label = "ballX")
    val animDy by animateDpAsState(dy.dp, label = "ballY")

    Box(
        modifier
            .size(132.dp)
            .scale(scale)
            .shadow((14 + glow * 22).dp, CircleShape, spotColor = FlowTheme.red, ambientColor = FlowTheme.red)
            .clip(CircleShape)
            .background(
                Brush.radialGradient(
                    listOf(FlowTheme.redLight, FlowTheme.red, FlowTheme.redDeep, FlowTheme.redDarkest),
                )
            ),
        contentAlignment = Alignment.Center,
    ) {
        Box(
            Modifier
                .offset(x = animDx, y = animDy)
                .size(34.dp)
                .clip(CircleShape)
                .background(
                    Brush.radialGradient(listOf(Color.White.copy(alpha = 0.95f), Color.Transparent))
                )
        )
    }
}
