package com.inomotech.canrosetta.companion

import com.inomotech.canrosetta.companion.time.TimeMath
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-JVM unit tests for the timestamp / unit math — the "whole ballgame" of
 * the recorder. No Android framework, no Robolectric.
 */
class TimeMathTest {

    @Test
    fun anchorIsWallClockMinusUptime() {
        // Boot happened 1000.5 s of monotonic time before wall clock 1_752_624_000.000.
        val wallMillis = 1_752_624_000_000L
        val elapsedNanos = 1_000_500_000_000L // 1000.5 s
        val anchor = TimeMath.anchorUtc(wallMillis, elapsedNanos)
        assertEquals(1_752_624_000.0 - 1000.5, anchor, 1e-6)
    }

    @Test
    fun utcFromElapsedAddsAnchor() {
        val anchor = 1_752_623_000.0
        // A sample 1234.567 s after boot.
        val tUtc = TimeMath.utcFromElapsedNanos(anchor, 1_234_567_000_000L)
        assertEquals(1_752_624_234.567, tUtc, 1e-6)
    }

    @Test
    fun relativeSpacingIsPreservedRegardlessOfAnchor() {
        // Two anchors differing by a big NTP step must not change the delta
        // between two samples — that is the reason we anchor once.
        val ns1 = 10_000_000_000L
        val ns2 = 10_050_000_000L // +50 ms
        val a = TimeMath.utcFromElapsedNanos(100.0, ns2) - TimeMath.utcFromElapsedNanos(100.0, ns1)
        val b = TimeMath.utcFromElapsedNanos(999999.0, ns2) - TimeMath.utcFromElapsedNanos(999999.0, ns1)
        assertEquals(0.050, a, 1e-9)
        // spacing is preserved to well within a microsecond regardless of the
        // anchor; it is NOT bit-identical, because double precision at
        // epoch-scale magnitudes has ~sub-µs ULP (harmless — the server aligns
        // to milliseconds, and this is why we anchor once rather than per-sample).
        assertEquals(a, b, 1e-6)
    }

    @Test
    fun millisToUtcDividesByThousand() {
        assertEquals(1_752_624_001.5, TimeMath.millisToUtc(1_752_624_001_500L), 1e-6)
    }

    @Test
    fun toGDividesByStandardGravity() {
        assertEquals(1.0, TimeMath.toG(9.80665f), 1e-6)
        assertEquals(0.0, TimeMath.toG(0f), 1e-9)
        assertEquals(-2.0, TimeMath.toG(-2f * 9.80665f), 1e-5)
    }
}
