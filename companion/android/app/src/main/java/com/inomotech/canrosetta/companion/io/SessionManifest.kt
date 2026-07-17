package com.inomotech.canrosetta.companion.io

import android.annotation.SuppressLint
import android.content.Context
import android.provider.Settings
import com.inomotech.canrosetta.companion.AppInfo
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.util.UUID

/**
 * Builds and writes `manifest.json`, matching `schemas/manifest.schema.json`.
 *
 * The companion device is `role: "companion"`, `kind: "android"`, with
 * `clock.source: "gps"`. Note: photos are NOT listed as a stream — the manifest
 * `streams[].kind` enum has no photo kind; stills self-describe via
 * `photos_index.jsonl`, exactly like the iOS app.
 */
object SessionManifest {

    /** One entry in `streams[]`. */
    data class Stream(
        val path: String,
        val kind: String,
        val rows: Long? = null,
        val index: String? = null,
        val tStartUtc: Double? = null,
        val tEndUtc: Double? = null,
    )

    /**
     * One entry in `sync_markers[]` (e.g. a triple brake-flash). `t_utc` is the
     * moment the driver pinned it; `count` is how many pulses (3 for the guided
     * "flash the brakes" step). Mirrors the iOS `Manifest.SyncMarker`.
     */
    data class SyncMarker(
        val kind: String,
        val tUtc: Double,
        val count: Int? = null,
    )

    fun build(
        sessionId: String,
        createdUtc: Double,
        deviceId: String,
        clockSource: String,
        utcOffsetEstS: Double?,
        errEstS: Double?,
        streams: List<Stream>,
        syncMarkers: List<SyncMarker> = emptyList(),
    ): JSONObject {
        val clock = JSONObject().put("source", clockSource)
        if (utcOffsetEstS != null) clock.put("utc_offset_est_s", utcOffsetEstS)
        if (errEstS != null) clock.put("err_est_s", errEstS)

        val device = JSONObject()
            .put("role", "companion")
            .put("kind", "android")
            .put("id", deviceId)
            .put("sw_version", AppInfo.SOFTWARE_VERSION)
            .put("clock", clock)

        val streamsArr = JSONArray()
        for (s in streams) {
            val so = JSONObject()
                .put("path", s.path)
                .put("kind", s.kind)
            if (s.rows != null) so.put("rows", s.rows)
            if (s.index != null) so.put("index", s.index)
            if (s.tStartUtc != null) so.put("t_start_utc", s.tStartUtc)
            if (s.tEndUtc != null) so.put("t_end_utc", s.tEndUtc)
            streamsArr.put(so)
        }

        val root = JSONObject()
            .put("schema_version", AppInfo.SCHEMA_VERSION)
            .put("session_id", sessionId)
            .put("created_utc", createdUtc)
            .put("devices", JSONArray().put(device))
            .put("streams", streamsArr)

        // Only emit sync_markers when there is at least one, matching iOS (which
        // encodes the field as nil/absent when empty).
        if (syncMarkers.isNotEmpty()) {
            val markers = JSONArray()
            for (m in syncMarkers) {
                val mo = JSONObject()
                    .put("kind", m.kind)
                    .put("t_utc", m.tUtc)
                if (m.count != null) mo.put("count", m.count)
                markers.put(mo)
            }
            root.put("sync_markers", markers)
        }
        return root
    }

    /**
     * A stable, non-PII device identifier for `devices[].id`. `ANDROID_ID` is
     * per-app-signing-key + per-user and resets on factory reset — enough to
     * distinguish phones in a merge without being a hardware serial.
     */
    @SuppressLint("HardwareIds")
    fun deviceId(context: Context): String {
        val raw = Settings.Secure.getString(context.contentResolver, Settings.Secure.ANDROID_ID)
        val id = if (!raw.isNullOrEmpty()) raw else UUID.randomUUID().toString()
        return "android-" + id.take(8).lowercase()
    }

    /** Serialise the manifest to [file] (pretty-printed, 2-space indent). */
    fun write(file: File, manifest: JSONObject) {
        file.writeText(manifest.toString(2), Charsets.UTF_8)
    }
}
