package com.inomotech.canrosetta.companion.time

/**
 * Pure (Android-free) time and unit math. Kept dependency-free so it is unit
 * testable on the JVM without Robolectric or the Android framework, and so the
 * timestamp conversion — the whole ballgame — is exercised directly.
 */
object TimeMath {

    /** Standard gravity, m/s^2. Used to convert accelerometer m/s^2 -> g. */
    const val STANDARD_GRAVITY = 9.80665

    /** Nanoseconds per second, as a double for fractional-second conversions. */
    const val NANOS_PER_SECOND = 1_000_000_000.0

    /**
     * The wall-clock UTC (Unix epoch seconds) that corresponds to the monotonic
     * clock's zero (device boot), captured once by sampling wall clock and the
     * monotonic uptime back-to-back:
     *
     *     bootWallClockUtc = wallClockMillis/1000 - elapsedRealtimeNanos/1e9
     */
    fun anchorUtc(wallClockMillis: Long, elapsedRealtimeNanos: Long): Double =
        wallClockMillis / 1000.0 - elapsedRealtimeNanos / NANOS_PER_SECOND

    /**
     * Convert a monotonic (elapsedRealtime-domain) nanosecond timestamp into
     * `t_utc` (Unix epoch seconds) using a fixed [bootWallClockUtc] anchor. Adding
     * a constant anchor means a later NTP/carrier step mid-drive does not change
     * the relative spacing between samples — only a constant offset the server
     * removes during fine alignment.
     */
    fun utcFromElapsedNanos(bootWallClockUtc: Double, elapsedNanos: Long): Double =
        bootWallClockUtc + elapsedNanos / NANOS_PER_SECOND

    /** Convert a wall-clock millisecond timestamp (e.g. a GPS fix time) to `t_utc`. */
    fun millisToUtc(millis: Long): Double = millis / 1000.0

    /** Convert an acceleration in m/s^2 to g. */
    fun toG(metersPerSecondSquared: Float): Double = metersPerSecondSquared / STANDARD_GRAVITY
}
