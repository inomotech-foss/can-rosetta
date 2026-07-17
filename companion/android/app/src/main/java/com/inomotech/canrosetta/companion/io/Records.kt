package com.inomotech.canrosetta.companion.io

import org.json.JSONArray
import org.json.JSONObject

/**
 * Builders for the newline-delimited-JSON records, using the bundled `org.json`.
 * Field names and units match the schemas in `/schemas` exactly:
 *
 * - motion: `{ t_utc, acc[3] (g), gravity[3] (g), rot[3] (rad/s), att[3]
 *   (roll,pitch,yaw rad), mag[3]|null (µT) }`
 * - location: `{ t_utc, lat, lon, alt, speed (m/s, -1 unknown),
 *   course (deg, -1 unknown), h_acc, v_acc }`
 * - video_index: `{ frame, pts, t_utc }`
 * - photo_index: `{ t_utc, path, w, h }`
 */
object Records {

    private fun array(values: DoubleArray): JSONArray {
        val a = JSONArray()
        for (v in values) a.put(v)
        return a
    }

    fun motion(
        tUtc: Double,
        acc: DoubleArray,
        gravity: DoubleArray?,
        rot: DoubleArray,
        att: DoubleArray,
        mag: DoubleArray?,
    ): JSONObject {
        val o = JSONObject()
        o.put("t_utc", tUtc)
        o.put("acc", array(acc))
        if (gravity != null) o.put("gravity", array(gravity))
        o.put("rot", array(rot))
        o.put("att", array(att))
        // Explicit null when the magnetometer is uncalibrated / unavailable
        // (schema type is array|null); never emit garbage values.
        o.put("mag", if (mag != null) array(mag) else JSONObject.NULL)
        return o
    }

    fun location(
        tUtc: Double,
        lat: Double,
        lon: Double,
        alt: Double,
        speed: Double,
        course: Double,
        hAcc: Double,
        vAcc: Double,
    ): JSONObject {
        val o = JSONObject()
        o.put("t_utc", tUtc)
        o.put("lat", lat)
        o.put("lon", lon)
        o.put("alt", alt)
        o.put("speed", speed)
        o.put("course", course)
        o.put("h_acc", hAcc)
        o.put("v_acc", vAcc)
        return o
    }

    fun videoIndex(frame: Int, pts: Double, tUtc: Double): JSONObject {
        val o = JSONObject()
        o.put("frame", frame)
        o.put("pts", pts)
        o.put("t_utc", tUtc)
        return o
    }

    fun photoIndex(tUtc: Double, path: String, w: Int?, h: Int?): JSONObject {
        val o = JSONObject()
        o.put("t_utc", tUtc)
        o.put("path", path)
        if (w != null) o.put("w", w)
        if (h != null) o.put("h", h)
        return o
    }
}
