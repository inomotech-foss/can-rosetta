package com.inomotech.canrosetta.companion.sensors

import android.annotation.SuppressLint
import android.content.Context
import android.location.Location
import android.os.Looper
import android.util.Log
import com.google.android.gms.location.FusedLocationProviderClient
import com.google.android.gms.location.LocationCallback
import com.google.android.gms.location.LocationRequest
import com.google.android.gms.location.LocationResult
import com.google.android.gms.location.LocationServices
import com.google.android.gms.location.Priority
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.io.Records
import com.inomotech.canrosetta.companion.time.TimeMath
import org.json.JSONObject

/**
 * Wraps the fused location provider at high accuracy for driving, mapping each
 * fix to a `phone/location.jsonl` record. Emits raw fixes only — no smoothing.
 *
 * Units follow the schema: `speed` m/s (`-1` if unknown), `course` deg from true
 * north (`-1` if unknown), `h_acc`/`v_acc` meters. `t_utc` comes from the fix's
 * own wall-clock time (`Location.time`), the best time available for a GPS sample.
 */
class LocationSource(context: Context) {

    private val client: FusedLocationProviderClient =
        LocationServices.getFusedLocationProviderClient(context)

    /** Called for every fix, on the main looper. */
    var onRecord: ((JSONObject) -> Unit)? = null

    /** Latest reported horizontal accuracy (m), or null if no valid fix yet. */
    @Volatile
    var lastHorizontalAccuracy: Double? = null
        private set

    private val callback = object : LocationCallback() {
        override fun onLocationResult(result: LocationResult) {
            for (loc in result.locations) emit(loc)
        }
    }

    private fun emit(loc: Location) {
        val speed = if (loc.hasSpeed()) loc.speed.toDouble() else -1.0
        val course = if (loc.hasBearing()) loc.bearing.toDouble() else -1.0
        val hAcc = if (loc.hasAccuracy()) loc.accuracy.toDouble() else -1.0
        val vAcc = if (loc.hasVerticalAccuracy()) loc.verticalAccuracyMeters.toDouble() else -1.0
        if (hAcc >= 0) lastHorizontalAccuracy = hAcc
        val record = Records.location(
            tUtc = TimeMath.millisToUtc(loc.time),
            lat = loc.latitude,
            lon = loc.longitude,
            alt = loc.altitude,
            speed = speed,
            course = course,
            hAcc = hAcc,
            vAcc = vAcc,
        )
        onRecord?.invoke(record)
    }

    @SuppressLint("MissingPermission")
    fun start() {
        val request = LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 1000L)
            .setMinUpdateIntervalMillis(100L)
            .build()
        try {
            client.requestLocationUpdates(request, callback, Looper.getMainLooper())
        } catch (e: SecurityException) {
            Log.e(AppInfo.TAG, "Cannot start location: permission missing (${e.message})")
        }
    }

    fun stop() {
        client.removeLocationUpdates(callback)
    }
}
