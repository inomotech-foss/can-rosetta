package com.inomotech.canrosetta.companion.time

import android.os.SystemClock
import java.util.UUID

/**
 * Provides honest UTC timestamps (`t_utc`, Unix epoch seconds as a [Double]) for
 * every recorded sample.
 *
 * We anchor once at construction against the monotonic clock
 * ([SystemClock.elapsedRealtimeNanos]) and the wall clock
 * ([System.currentTimeMillis]). Every sensor sample already carries a monotonic
 * timestamp (`SensorEvent.timestamp`, CameraX `ImageInfo.timestamp`) measured
 * against the same boot instant, so:
 *
 *     t_utc = bootWallClockUtc + sampleElapsedNanos/1e9
 *
 * Because the anchor is captured once and never updated, a later NTP step does
 * not change the relative spacing between samples — only a constant offset the
 * server estimates and removes during fine alignment. This is the
 * "monotonic-anchored wall clock" the data-format doc asks producers for.
 *
 * We report `clock.source = "gps"` in the manifest (full-accuracy GNSS runs the
 * whole session). Honest caveat: Android does not expose raw GNSS/PPS time to
 * apps, so the absolute offset is really the (NTP/carrier-disciplined) system
 * clock's; we report `err_est_s = 0.1` rather than claim GPS-locked accuracy.
 */
class Clock {

    /** Wall-clock UTC (epoch seconds) corresponding to the monotonic clock zero. */
    val bootWallClockUtc: Double

    init {
        val elapsed = SystemClock.elapsedRealtimeNanos()
        val wall = System.currentTimeMillis()
        bootWallClockUtc = TimeMath.anchorUtc(wall, elapsed)
    }

    /** Convert a monotonic (elapsedRealtime-domain) nanosecond timestamp to `t_utc`. */
    fun utcFromElapsedNanos(elapsedNanos: Long): Double =
        TimeMath.utcFromElapsedNanos(bootWallClockUtc, elapsedNanos)

    /** "Now" as `t_utc`, using the monotonic-anchored clock (not a fresh wall read). */
    fun nowUtc(): Double =
        TimeMath.utcFromElapsedNanos(bootWallClockUtc, SystemClock.elapsedRealtimeNanos())
}

/**
 * Session identity. In the full system the `session_id` is agreed with the
 * AutoPi (the phone mints it and sends it via `POST /api/session`) so both parts
 * merge on the server. We generate a lowercase UUID by default.
 */
object SessionId {
    fun generate(): String = UUID.randomUUID().toString().lowercase()
}
