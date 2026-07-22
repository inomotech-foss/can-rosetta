package com.inomotech.canrosetta.companion.car

import androidx.car.app.hardware.common.CarValue
import org.json.JSONObject

/**
 * Builders for `phone/car_hw.jsonl` — the car-hardware reference stream that
 * answers whether the head unit (e.g. the eVito's MBUX) forwards vehicle data
 * to Android Auto at all. One JSON object per line:
 *
 * ```
 * {"t_utc": <unix s, phone clock — same domain as motion.jsonl>,
 *  "kind": "<model|energy|speed|mileage|location|accelerometer|gyroscope|compass>",
 *  "status": "<success|unavailable|unimplemented|error>",
 *  "data": {...}}
 * ```
 *
 * A record is emitted for EVERY callback/fetch INCLUDING non-success statuses:
 * a stream full of "unimplemented" is exactly as valuable as one full of
 * speeds — the availability answer is itself the deliverable. `data` fields
 * are nullable per the shared contract; unknown fields are omitted, never
 * emitted as garbage.
 *
 * Pure `org.json` + [CarValue] constants only (no Android framework, no car
 * host), so the status mapping and record shapes are unit-testable on the JVM.
 */
object CarHwRecords {

    // Contract status strings.
    const val STATUS_SUCCESS = "success"
    const val STATUS_UNAVAILABLE = "unavailable"
    const val STATUS_UNIMPLEMENTED = "unimplemented"
    const val STATUS_ERROR = "error"

    /**
     * Map one [CarValue] status int to the contract's status string.
     * `STATUS_UNKNOWN` (and any future constant) maps to "error": the host
     * answered but the answer is meaningless, which is neither a clean
     * "unavailable" nor "unimplemented".
     */
    fun statusString(carValueStatus: Int): String = when (carValueStatus) {
        CarValue.STATUS_SUCCESS -> STATUS_SUCCESS
        CarValue.STATUS_UNAVAILABLE -> STATUS_UNAVAILABLE
        CarValue.STATUS_UNIMPLEMENTED -> STATUS_UNIMPLEMENTED
        else -> STATUS_ERROR
    }

    /**
     * Aggregate the statuses of a multi-[CarValue] callback (e.g. energy has
     * battery/fuel/range/low as four independent values) into the record's one
     * status. Most-informative-first: any success makes the record a success
     * (the successful fields are present, the rest omitted); otherwise any
     * "unavailable" (the car HAS the channel, just not right now) beats
     * "unimplemented" (the channel does not exist); otherwise error.
     */
    fun aggregateStatus(statuses: List<Int>): String = when {
        statuses.any { it == CarValue.STATUS_SUCCESS } -> STATUS_SUCCESS
        statuses.any { it == CarValue.STATUS_UNAVAILABLE } -> STATUS_UNAVAILABLE
        statuses.any { it == CarValue.STATUS_UNIMPLEMENTED } -> STATUS_UNIMPLEMENTED
        else -> STATUS_ERROR
    }

    /** The one record shape; all kind-specific builders funnel through here. */
    fun record(tUtc: Double, kind: String, status: String, data: JSONObject): JSONObject =
        JSONObject()
            .put("t_utc", tUtc)
            .put("kind", kind)
            .put("status", status)
            .put("data", data)

    fun model(tUtc: Double, status: String, make: String?, model: String?, year: Int?): JSONObject =
        record(tUtc, "model", status, JSONObject().apply {
            if (make != null) put("make", make)
            if (model != null) put("model", model)
            if (year != null) put("year", year)
        })

    fun energy(
        tUtc: Double,
        status: String,
        batteryPercent: Double?,
        fuelPercent: Double?,
        rangeMeters: Double?,
        energyLow: Boolean?,
    ): JSONObject = record(tUtc, "energy", status, JSONObject().apply {
        if (batteryPercent != null) put("battery_percent", batteryPercent)
        if (fuelPercent != null) put("fuel_percent", fuelPercent)
        if (rangeMeters != null) put("range_meters", rangeMeters)
        if (energyLow != null) put("energy_low", energyLow)
    })

    fun speed(tUtc: Double, status: String, rawMps: Double?, displayMps: Double?): JSONObject =
        record(tUtc, "speed", status, JSONObject().apply {
            if (rawMps != null) put("raw_mps", rawMps)
            if (displayMps != null) put("display_mps", displayMps)
        })

    fun mileage(tUtc: Double, status: String, odometerMeters: Double?): JSONObject =
        record(tUtc, "mileage", status, JSONObject().apply {
            if (odometerMeters != null) put("odometer_meters", odometerMeters)
        })

    fun location(
        tUtc: Double,
        status: String,
        lat: Double?,
        lon: Double?,
        accuracyM: Double?,
        speedMps: Double?,
        bearingDeg: Double?,
    ): JSONObject = record(tUtc, "location", status, JSONObject().apply {
        if (lat != null) put("lat", lat)
        if (lon != null) put("lon", lon)
        if (accuracyM != null) put("accuracy_m", accuracyM)
        if (speedMps != null) put("speed_mps", speedMps)
        if (bearingDeg != null) put("bearing_deg", bearingDeg)
    })

    /** Shared shape for the three axis sensors: accelerometer/gyroscope/compass. */
    fun axes(tUtc: Double, kind: String, status: String, x: Double?, y: Double?, z: Double?): JSONObject =
        record(tUtc, kind, status, JSONObject().apply {
            if (x != null) put("x", x)
            if (y != null) put("y", y)
            if (z != null) put("z", z)
        })

    /**
     * The record emitted instead of a listener registration when the runtime
     * permission is missing — still a record, because "the driver never granted
     * car-data access" is an availability answer too.
     */
    fun permissionDenied(tUtc: Double, kind: String): JSONObject =
        record(tUtc, kind, STATUS_UNAVAILABLE, JSONObject().put("reason", "permission_denied"))

    /** Registration/fetch failed outright (host exception etc.). */
    fun error(tUtc: Double, kind: String, reason: String?): JSONObject =
        record(tUtc, kind, STATUS_ERROR, JSONObject().apply {
            if (reason != null) put("reason", reason)
        })
}
