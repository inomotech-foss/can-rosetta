package com.inomotech.canrosetta.companion.car

import androidx.car.app.hardware.common.CarValue
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Pure-JVM tests for the `car_hw.jsonl` record builders: the CarValue-status →
 * contract-status mapping and the exact record envelope. [CarValue] status
 * constants are compile-time ints and org.json comes from the real library
 * (test dependency), so no Android framework is involved.
 */
class CarHwRecordsTest {

    // MARK: - Status mapping

    @Test
    fun statusStringMapsEveryCarValueStatus() {
        assertEquals("success", CarHwRecords.statusString(CarValue.STATUS_SUCCESS))
        assertEquals("unavailable", CarHwRecords.statusString(CarValue.STATUS_UNAVAILABLE))
        assertEquals("unimplemented", CarHwRecords.statusString(CarValue.STATUS_UNIMPLEMENTED))
        // UNKNOWN (and anything future) is an answer without meaning → error.
        assertEquals("error", CarHwRecords.statusString(CarValue.STATUS_UNKNOWN))
        assertEquals("error", CarHwRecords.statusString(999))
    }

    @Test
    fun aggregateStatusPrefersSuccessThenUnavailableThenUnimplemented() {
        // Any success wins: a partially-populated callback is a success record.
        assertEquals(
            "success",
            CarHwRecords.aggregateStatus(
                listOf(CarValue.STATUS_UNIMPLEMENTED, CarValue.STATUS_SUCCESS)
            ),
        )
        // "Channel exists but is silent" beats "channel does not exist".
        assertEquals(
            "unavailable",
            CarHwRecords.aggregateStatus(
                listOf(CarValue.STATUS_UNIMPLEMENTED, CarValue.STATUS_UNAVAILABLE)
            ),
        )
        assertEquals(
            "unimplemented",
            CarHwRecords.aggregateStatus(
                listOf(CarValue.STATUS_UNIMPLEMENTED, CarValue.STATUS_UNIMPLEMENTED)
            ),
        )
        assertEquals(
            "error",
            CarHwRecords.aggregateStatus(listOf(CarValue.STATUS_UNKNOWN)),
        )
    }

    // MARK: - Record envelope

    @Test
    fun modelRecordCarriesContractEnvelope() {
        val rec = CarHwRecords.model(1_752_624_000.5, "success", "Mercedes-Benz", "eVito", 2022)
        assertEquals(1_752_624_000.5, rec.getDouble("t_utc"), 1e-9)
        assertEquals("model", rec.getString("kind"))
        assertEquals("success", rec.getString("status"))
        val data = rec.getJSONObject("data")
        assertEquals("Mercedes-Benz", data.getString("make"))
        assertEquals("eVito", data.getString("model"))
        assertEquals(2022, data.getInt("year"))
    }

    @Test
    fun unknownFieldsAreOmittedNotNulled() {
        // Contract: all data fields nullable, unknown fields OMITTED.
        val rec = CarHwRecords.speed(1.0, "success", 13.9, null)
        val data = rec.getJSONObject("data")
        assertEquals(13.9, data.getDouble("raw_mps"), 1e-9)
        assertFalse(data.has("display_mps"))
    }

    @Test
    fun nonSuccessCallbackStillProducesAFullRecord() {
        // The availability answer is the deliverable: an all-unimplemented
        // callback yields a well-formed record with an empty data object.
        val rec = CarHwRecords.mileage(2.0, "unimplemented", null)
        assertEquals("mileage", rec.getString("kind"))
        assertEquals("unimplemented", rec.getString("status"))
        assertEquals(0, rec.getJSONObject("data").length())
    }

    @Test
    fun permissionDeniedIsAnUnavailableRecordWithReason() {
        val rec = CarHwRecords.permissionDenied(3.0, "speed")
        assertEquals("speed", rec.getString("kind"))
        assertEquals("unavailable", rec.getString("status"))
        assertEquals("permission_denied", rec.getJSONObject("data").getString("reason"))
    }

    @Test
    fun axesRecordUsesXyzKeys() {
        val rec = CarHwRecords.axes(4.0, "gyroscope", "success", 0.1, -0.2, 0.3)
        val data = rec.getJSONObject("data")
        assertEquals(0.1, data.getDouble("x"), 1e-9)
        assertEquals(-0.2, data.getDouble("y"), 1e-9)
        assertEquals(0.3, data.getDouble("z"), 1e-9)
    }

    @Test
    fun locationRecordUsesContractFieldNames() {
        val rec = CarHwRecords.location(5.0, "success", 48.1, 11.5, 3.0, 13.9, 270.0)
        val data = rec.getJSONObject("data")
        assertTrue(data.has("lat"))
        assertTrue(data.has("lon"))
        assertTrue(data.has("accuracy_m"))
        assertTrue(data.has("speed_mps"))
        assertTrue(data.has("bearing_deg"))
    }

    @Test
    fun energyRecordUsesContractFieldNames() {
        val rec = CarHwRecords.energy(6.0, "success", 81.5, null, 210_000.0, false)
        val data = rec.getJSONObject("data")
        assertEquals(81.5, data.getDouble("battery_percent"), 1e-9)
        assertFalse(data.has("fuel_percent"))
        assertEquals(210_000.0, data.getDouble("range_meters"), 1e-9)
        assertFalse(data.getBoolean("energy_low"))
    }
}
