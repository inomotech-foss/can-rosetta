package com.inomotech.canrosetta.companion.ui.flow

import androidx.compose.animation.core.RepeatMode
import androidx.compose.animation.core.animateFloat
import androidx.compose.animation.core.infiniteRepeatable
import androidx.compose.animation.core.rememberInfiniteTransition
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** Shared, FlowTheme-styled building blocks for the drive-flow screens. */

val Mono: FontFamily = FontFamily.Monospace

@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier) {
    Text(
        text.uppercase(),
        modifier = modifier,
        color = FlowTheme.textSecondary,
        fontSize = 12.sp,
        fontWeight = FontWeight.Medium,
        letterSpacing = 0.6.sp,
    )
}

@Composable
fun FlowCard(
    modifier: Modifier = Modifier,
    padding: PaddingValues = PaddingValues(0.dp),
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(
        modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(FlowTheme.cardRadius))
            .background(FlowTheme.card)
            .padding(padding),
        content = content,
    )
}

@Composable
fun RowSeparator(leadingInset: Int = 16) {
    Box(
        Modifier
            .fillMaxWidth()
            .padding(start = leadingInset.dp)
            .height(0.5.dp)
            .background(FlowTheme.separator)
    )
}

@Composable
fun InfoRow(label: String, value: String, valueColor: Color = FlowTheme.textSecondary) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp)
            .height(50.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(label, color = FlowTheme.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
        Text(value, color = valueColor, fontSize = 13.sp, fontFamily = Mono)
    }
}

@Composable
fun CheckRow(ok: Boolean, label: String, detail: String) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 8.dp)
            .height(40.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier
                .size(26.dp)
                .clip(CircleShape)
                .background(if (ok) FlowTheme.greenFill else FlowTheme.amberFill),
            contentAlignment = Alignment.Center,
        ) {
            Text(
                if (ok) "✓" else "!",
                color = if (ok) FlowTheme.green else FlowTheme.amber,
                fontSize = 13.sp,
                fontWeight = FontWeight.Bold,
            )
        }
        Text(label, color = FlowTheme.text, fontSize = 15.sp, modifier = Modifier.weight(1f))
        Text(
            detail,
            color = if (ok) FlowTheme.textSecondary else FlowTheme.amber,
            fontSize = 12.sp,
            fontFamily = Mono,
            textAlign = TextAlign.End,
        )
    }
}

@Composable
fun PrimaryButton(
    text: String,
    modifier: Modifier = Modifier,
    background: Color = FlowTheme.indigo,
    textColor: Color = Color.White,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Box(
        modifier
            .fillMaxWidth()
            .height(FlowTheme.buttonHeight)
            .clip(RoundedCornerShape(FlowTheme.buttonRadius))
            .background(background.copy(alpha = if (enabled) 1f else 0.5f))
            .clickable(enabled = enabled) { onClick() },
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = textColor, fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
    }
}

@Composable
fun SecondaryButton(text: String, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Box(
        modifier
            .fillMaxWidth()
            .height(FlowTheme.buttonHeight)
            .clip(RoundedCornerShape(FlowTheme.buttonRadius))
            .background(FlowTheme.whiteSubtle)
            .clickable { onClick() },
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = FlowTheme.text, fontSize = 15.sp, fontWeight = FontWeight.Medium)
    }
}

@Composable
fun StatusPill(text: String, dotColor: Color, fill: Color, fg: Color, blinkingDot: Boolean = false) {
    val alpha = if (blinkingDot) {
        val t = rememberInfiniteTransition(label = "blink")
        t.animateFloat(
            initialValue = 1f,
            targetValue = 0.25f,
            animationSpec = infiniteRepeatable(tween(1200), RepeatMode.Reverse),
            label = "blinkAlpha",
        ).value
    } else {
        1f
    }
    Row(
        Modifier
            .clip(CircleShape)
            .background(fill)
            .padding(horizontal = 12.dp, vertical = 5.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(7.dp),
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(dotColor).alpha(alpha))
        Text(text, color = fg, fontSize = 12.sp, fontWeight = FontWeight.Bold, letterSpacing = 1.sp)
    }
}

@Composable
fun Chip(text: String) {
    Box(
        Modifier
            .clip(RoundedCornerShape(6.dp))
            .background(FlowTheme.indigo.copy(alpha = 0.18f))
            .padding(horizontal = 8.dp, vertical = 3.dp),
    ) {
        Text(text, color = FlowTheme.indigoLight, fontSize = 12.sp, fontFamily = Mono)
    }
}
