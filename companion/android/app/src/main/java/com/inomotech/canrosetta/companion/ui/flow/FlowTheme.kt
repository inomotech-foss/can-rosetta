package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import java.io.File

/**
 * b-on "Midnight" palette + shared metrics for the drive flow — the Compose
 * translation of the iOS `Theme` enum, kept token-for-token so both apps read the
 * same.
 */
object FlowTheme {
    // Surfaces
    val pageBg = Color(0xFF030712)
    val card = Color(0xFF1C1C1E)

    // Text
    val text = Color.White
    val textSecondary = Color.White.copy(alpha = 0.6f)
    val textMuted = Color.White.copy(alpha = 0.45f)

    // Accents
    val indigo = Color(0xFF6366F1)
    val indigoLight = Color(0xFFA5B4FC)
    val indigoSubtleFill = Color(0xFF6366F1).copy(alpha = 0.14f)
    val indigoSubtleBorder = Color(0xFF6366F1).copy(alpha = 0.35f)

    val green = Color(0xFF4ADE80)
    val greenFill = Color(0xFF22C55E).copy(alpha = 0.16f)

    val red = Color(0xFFEF4444)
    val redLight = Color(0xFFF87171)
    val redFill = Color(0xFFEF4444).copy(alpha = 0.16f)
    val redDeep = Color(0xFF7F1D1D)
    val redDarkest = Color(0xFF2A0606)

    val amber = Color(0xFFFBBF24)
    val amberFill = Color(0xFFF59E0B).copy(alpha = 0.16f)

    // Inset row separators (white 0.33 · alpha 0.65).
    val separator = Color(0xFF545454).copy(alpha = 0.65f)

    // A subtle white fill used for the manual-pairing fields / pending glyphs.
    val whiteSubtle = Color.White.copy(alpha = 0.06f)
    val whiteFaint = Color.White.copy(alpha = 0.10f)

    // Metrics
    val cardRadius = 22.dp
    val buttonRadius = 14.dp
    val buttonHeight = 50.dp
}

/** Formatting helpers mirroring the iOS `Fmt` enum. */
object Fmt {
    fun hms(seconds: Double): String {
        val s = seconds.coerceAtLeast(0.0).toInt()
        return "%02d:%02d:%02d".format(s / 3600, (s % 3600) / 60, s % 60)
    }

    /** Human distance: metres under 1 km, else km with one decimal. */
    fun distance(meters: Double): String =
        if (meters < 1000) "%.0f m".format(meters) else "%.1f km".format(meters / 1000)

    fun gbFree(bytes: Long): String = "%.0f GB free".format(bytes / 1_000_000_000.0)

    fun fileSize(file: File): String? {
        if (!file.exists()) return null
        val size = file.length()
        val mb = size / 1_000_000.0
        return if (mb < 1) "%.0f KB".format(size / 1000.0) else "%.1f MB".format(mb)
    }
}
