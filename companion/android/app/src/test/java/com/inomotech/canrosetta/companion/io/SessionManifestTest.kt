package com.inomotech.canrosetta.companion.io

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Pure-JVM test that the manifest serialises the new `car_hw` stream exactly
 * like motion/location (path/kind/rows/time bounds). Uses the real org.json
 * (test dependency); [SessionManifest.build] itself needs no Android context.
 */
class SessionManifestTest {

    @Test
    fun carHwStreamSerialisesLikeMotionAndLocation() {
        val manifest = SessionManifest.build(
            sessionId = "test-session",
            createdUtc = 1_752_624_000.0,
            deviceId = "android-deadbeef",
            clockSource = "gps",
            utcOffsetEstS = 0.0,
            errEstS = 0.1,
            streams = listOf(
                SessionManifest.Stream(
                    "phone/motion.jsonl", "motion", 1000, null, 1_752_624_000.0, 1_752_624_060.0),
                SessionManifest.Stream(
                    "phone/location.jsonl", "location", 60, null, 1_752_624_000.0, 1_752_624_060.0),
                SessionManifest.Stream(
                    "phone/car_hw.jsonl", "car_hw", 42, null, 1_752_624_000.0, 1_752_624_060.0),
            ),
        )

        val streams = manifest.getJSONArray("streams")
        assertEquals(3, streams.length())

        val carHw = streams.getJSONObject(2)
        assertEquals("phone/car_hw.jsonl", carHw.getString("path"))
        assertEquals("car_hw", carHw.getString("kind"))
        assertEquals(42L, carHw.getLong("rows"))
        assertEquals(1_752_624_000.0, carHw.getDouble("t_start_utc"), 1e-6)
        assertEquals(1_752_624_060.0, carHw.getDouble("t_end_utc"), 1e-6)

        // Same field set as the established streams — no accidental extras.
        val motion = streams.getJSONObject(0)
        assertEquals(motion.keySet(), carHw.keySet())
    }
}
