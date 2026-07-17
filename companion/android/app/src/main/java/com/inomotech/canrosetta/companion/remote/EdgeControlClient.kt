package com.inomotech.canrosetta.companion.remote

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.channels.awaitClose
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.callbackFlow
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import org.json.JSONObject
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.TimeUnit

// MARK: - Wire models (match docs/control-protocol.md)

enum class EdgeMode(val wire: String, val label: String) {
    FAST("fast", "Fast"),
    SLOW("slow", "Slow");

    companion object {
        fun fromWire(value: String?): EdgeMode = entries.firstOrNull { it.wire == value } ?: FAST
    }
}

data class HealthResponse(val ok: Boolean, val swVersion: String?)
data class TimeResponse(val tUtc: Double)
data class EdgeDevice(val id: String?, val swVersion: String?)
data class EdgeStats(val elapsedS: Double?, val frames: Int?, val obdSamples: Int?)
data class DiscoverySummary(val obdPids: Int?, val udsDids: Int?, val plainCanIds: Int?)

data class StatusResponse(
    val state: String,
    val sessionId: String?,
    val outputDir: String?,
    val device: EdgeDevice?,
    val mode: String?,
    val stats: EdgeStats?,
    val discoverySummary: DiscoverySummary?,
    val error: String?,
)

data class SessionResponse(val sessionId: String, val outputDir: String?, val device: EdgeDevice?)
data class CommandResponse(val state: String?, val frames: Int?)

data class EdgeEvent(
    val event: String,
    val state: String? = null,
    val phase: String? = null,
    val supportedPids: Int? = null,
    val summary: DiscoverySummary? = null,
    val frames: Int? = null,
    val obdSamples: Int? = null,
    val elapsedS: Double? = null,
    val message: String? = null,
    val ts: Double? = null,
)

/** A user-presentable error from the control client. */
class EdgeException(message: String, val statusCode: Int? = null) : Exception(message)

/**
 * Client for the AutoPi control protocol (`docs/control-protocol.md`) over
 * OkHttp. Holds only host + token, so it can be recreated cheaply whenever the
 * pairing settings change (the OkHttp clients themselves are shared singletons).
 *
 * HTTP requests carry `Authorization: Bearer <token>`; the WebSocket carries the
 * token both as an `Authorization` header and a `?token=` query param.
 */
class EdgeControlClient(host: String, private val token: String) {

    private val baseUrl: String = host.trim().trimEnd('/')

    companion object {
        private val JSON = "application/json; charset=utf-8".toMediaType()

        private val httpClient: OkHttpClient by lazy {
            OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .readTimeout(15, TimeUnit.SECONDS)
                .callTimeout(20, TimeUnit.SECONDS)
                .build()
        }

        private val wsClient: OkHttpClient by lazy {
            OkHttpClient.Builder()
                .connectTimeout(10, TimeUnit.SECONDS)
                .pingInterval(20, TimeUnit.SECONDS)
                .readTimeout(0, TimeUnit.SECONDS) // keep the socket open indefinitely
                .build()
        }
    }

    // MARK: HTTP endpoints

    suspend fun health(): HealthResponse {
        val o = getJson("/api/health")
        return HealthResponse(o.optBoolean("ok", false), o.optStringOrNull("sw_version"))
    }

    suspend fun time(): TimeResponse = TimeResponse(getJson("/api/time").getDouble("t_utc"))

    suspend fun status(): StatusResponse = parseStatus(getJson("/api/status"))

    suspend fun createSession(
        sessionId: String?,
        edgeUtcOffsetEstS: Double?,
        clockSource: String?,
    ): SessionResponse {
        val body = JSONObject()
        if (sessionId != null) body.put("session_id", sessionId)
        if (edgeUtcOffsetEstS != null) body.put("edge_utc_offset_est_s", edgeUtcOffsetEstS)
        if (clockSource != null) body.put("clock_source", clockSource)
        val o = postJson("/api/session", body)
        return SessionResponse(
            sessionId = o.optString("session_id", sessionId ?: ""),
            outputDir = o.optStringOrNull("output_dir"),
            device = parseDevice(o.optJSONObject("device")),
        )
    }

    suspend fun discover(mode: EdgeMode): CommandResponse =
        parseCommand(postJson("/api/discover", JSONObject().put("mode", mode.wire)))

    suspend fun startLog(): CommandResponse = parseCommand(postJson("/api/log/start", null))

    suspend fun stopLog(): CommandResponse = parseCommand(postJson("/api/log/stop", null))

    suspend fun run(mode: EdgeMode, durationS: Double? = null): CommandResponse {
        val body = JSONObject().put("mode", mode.wire)
        body.put("duration_s", if (durationS != null) durationS else JSONObject.NULL)
        return parseCommand(postJson("/api/run", body))
    }

    // MARK: WebSocket event stream

    /**
     * Live event stream from `GET /api/ws`. Each socket frame may carry one or
     * more newline-delimited JSON events. The flow completes when the socket
     * closes and errors when it fails, so the caller can fall back to polling.
     */
    fun events(): Flow<EdgeEvent> = callbackFlow {
        val listener = object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                for (line in text.split("\n")) {
                    val trimmed = line.trim()
                    if (trimmed.isEmpty()) continue
                    try {
                        trySend(parseEvent(JSONObject(trimmed)))
                    } catch (_: Exception) {
                        // ignore malformed lines
                    }
                }
            }

            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                onMessage(webSocket, bytes.utf8())
            }

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
                close(t)
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, reason)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
                close()
            }
        }
        val ws = wsClient.newWebSocket(buildWsRequest(), listener)
        awaitClose { ws.cancel() }
    }

    // MARK: Request plumbing

    private suspend fun getJson(path: String): JSONObject = execute(buildRequest(path, "GET", null))

    private suspend fun postJson(path: String, body: JSONObject?): JSONObject =
        execute(buildRequest(path, "POST", body))

    private fun buildRequest(path: String, method: String, body: JSONObject?): Request {
        val builder = Request.Builder().url(baseUrl + path)
        if (token.isNotEmpty()) builder.header("Authorization", "Bearer $token")
        when (method) {
            "POST" -> builder.post((body?.toString() ?: "{}").toRequestBody(JSON))
            else -> builder.get()
        }
        return builder.build()
    }

    private fun buildWsRequest(): Request {
        val url = buildString {
            append(baseUrl).append("/api/ws")
            if (token.isNotEmpty()) append("?token=").append(URLEncoder.encode(token, "UTF-8"))
        }
        val builder = Request.Builder().url(url)
        if (token.isNotEmpty()) builder.header("Authorization", "Bearer $token")
        return builder.build()
    }

    private suspend fun execute(request: Request): JSONObject = withContext(Dispatchers.IO) {
        val response = try {
            httpClient.newCall(request).execute()
        } catch (e: IOException) {
            throw EdgeException("Network error: ${e.message}")
        }
        response.use { resp ->
            val bodyStr = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) throw EdgeException(messageForStatus(resp.code), resp.code)
            if (bodyStr.isBlank()) JSONObject() else JSONObject(bodyStr)
        }
    }

    private fun messageForStatus(code: Int): String = when (code) {
        401 -> "Unauthorized — check the bearer token"
        404 -> "Not found (404)"
        409 -> "AutoPi is busy — a job is already running (409)"
        else -> "AutoPi returned HTTP $code"
    }

    // MARK: Parsing

    private fun parseDevice(o: JSONObject?): EdgeDevice? =
        o?.let { EdgeDevice(it.optStringOrNull("id"), it.optStringOrNull("sw_version")) }

    private fun parseSummary(o: JSONObject?): DiscoverySummary? =
        o?.let {
            DiscoverySummary(
                it.optIntOrNull("obd_pids"),
                it.optIntOrNull("uds_dids"),
                it.optIntOrNull("plain_can_ids"),
            )
        }

    private fun parseStatus(o: JSONObject): StatusResponse = StatusResponse(
        state = o.optString("state", "unknown"),
        sessionId = o.optStringOrNull("session_id"),
        outputDir = o.optStringOrNull("output_dir"),
        device = parseDevice(o.optJSONObject("device")),
        mode = o.optStringOrNull("mode"),
        stats = o.optJSONObject("stats")?.let {
            EdgeStats(it.optDoubleOrNull("elapsed_s"), it.optIntOrNull("frames"), it.optIntOrNull("obd_samples"))
        },
        discoverySummary = parseSummary(o.optJSONObject("discovery_summary")),
        error = o.optStringOrNull("error"),
    )

    private fun parseCommand(o: JSONObject): CommandResponse =
        CommandResponse(o.optStringOrNull("state"), o.optIntOrNull("frames"))

    private fun parseEvent(o: JSONObject): EdgeEvent = EdgeEvent(
        event = o.optString("event", ""),
        state = o.optStringOrNull("state"),
        phase = o.optStringOrNull("phase"),
        supportedPids = o.optIntOrNull("supported_pids"),
        summary = parseSummary(o.optJSONObject("summary")),
        frames = o.optIntOrNull("frames"),
        obdSamples = o.optIntOrNull("obd_samples"),
        elapsedS = o.optDoubleOrNull("elapsed_s"),
        message = o.optStringOrNull("message"),
        ts = o.optDoubleOrNull("ts"),
    )
}

// org.json helpers that return null (not sentinel defaults) for absent/null keys.
private fun JSONObject.optStringOrNull(key: String): String? =
    if (has(key) && !isNull(key)) optString(key) else null

private fun JSONObject.optIntOrNull(key: String): Int? =
    if (has(key) && !isNull(key)) optInt(key) else null

private fun JSONObject.optDoubleOrNull(key: String): Double? =
    if (has(key) && !isNull(key)) optDouble(key) else null
