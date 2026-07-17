package com.inomotech.canrosetta.companion.remote

import android.content.Context
import android.util.Log
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.time.Clock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlin.coroutines.cancellation.CancellationException

enum class ConnState { IDLE, CONNECTING, CONNECTED, FAILED }

/** Live remote-control state the UI observes. */
data class EdgeUiState(
    val conn: ConnState = ConnState.IDLE,
    val connMessage: String? = null,
    val swVersion: String? = null,
    val edgeState: String = "unknown",
    val frames: Int = 0,
    val obdSamples: Int = 0,
    val elapsed: Double = 0.0,
    val discoverySummary: DiscoverySummary? = null,
    val timeOffset: Double? = null,
    val timeRoundTrip: Double? = null,
    val wsConnected: Boolean = false,
    val isBusy: Boolean = false,
    val lastError: String? = null,
)

/**
 * Owns the phone side of the control channel: host/token/mode (persisted in
 * SharedPreferences), the measured edge/companion clock offset, the live edge
 * status, and the coordinated start/stop that drives [RecordingController] in
 * lock-step with the AutoPi.
 *
 * The `session_id` is single-sourced from [RecordingController.sessionId] (the
 * phone mints it). The coordinated start sends that same id to the AutoPi via
 * `POST /api/session`, so both halves merge server-side.
 */
class EdgeConnection(context: Context) {

    private val prefs = context.getSharedPreferences("edge", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())
    private val clock = Clock()

    val host = MutableStateFlow(prefs.getString(KEY_HOST, DEFAULT_HOST) ?: DEFAULT_HOST)
    val token = MutableStateFlow(prefs.getString(KEY_TOKEN, "") ?: "")
    val mode = MutableStateFlow(EdgeMode.fromWire(prefs.getString(KEY_MODE, "fast")))

    private val _state = MutableStateFlow(EdgeUiState())
    val state: StateFlow<EdgeUiState> = _state

    private var wsJob: Job? = null
    private var pollJob: Job? = null

    fun setHost(value: String) {
        host.value = value
        prefs.edit().putString(KEY_HOST, value).apply()
    }

    fun setToken(value: String) {
        token.value = value
        prefs.edit().putString(KEY_TOKEN, value).apply()
    }

    fun setMode(value: EdgeMode) {
        mode.value = value
        prefs.edit().putString(KEY_MODE, value.wire).apply()
    }

    fun isConfigured(): Boolean = host.value.trim().isNotEmpty()

    /** True once the edge health check has succeeded (used by the drive flow). */
    fun isConnected(): Boolean = _state.value.conn == ConnState.CONNECTED

    private fun client(): EdgeControlClient = EdgeControlClient(host.value, token.value)

    // MARK: - Pairing / health

    fun checkHealth() {
        scope.launch {
            if (!isConfigured()) {
                update { it.copy(conn = ConnState.FAILED, connMessage = "Enter the AutoPi host first") }
                return@launch
            }
            update { it.copy(conn = ConnState.CONNECTING, lastError = null) }
            try {
                val health = client().health()
                update {
                    it.copy(
                        swVersion = health.swVersion,
                        conn = if (health.ok) ConnState.CONNECTED else ConnState.FAILED,
                        connMessage = if (health.ok) null else "AutoPi reported not-ok",
                    )
                }
                if (health.ok) refreshStatus()
            } catch (e: Exception) {
                update { it.copy(conn = ConnState.FAILED, connMessage = errorMessage(e)) }
            }
        }
    }

    /**
     * The drive-flow "Pair" action: run the health check and, on success, a
     * Cristian time-sync in one coroutine, so the Pair screen can show
     * "handshake complete" with the offset/rtt. Mirrors the iOS `pairManually()`.
     */
    fun pair() {
        scope.launch {
            if (!isConfigured()) {
                update { it.copy(conn = ConnState.FAILED, connMessage = "Enter the AutoPi host first") }
                return@launch
            }
            update { it.copy(conn = ConnState.CONNECTING, lastError = null) }
            try {
                val health = client().health()
                update {
                    it.copy(
                        swVersion = health.swVersion,
                        conn = if (health.ok) ConnState.CONNECTED else ConnState.FAILED,
                        connMessage = if (health.ok) null else "AutoPi reported not-ok",
                    )
                }
                if (health.ok) {
                    refreshStatus()
                    performTimeSync(5)
                }
            } catch (e: Exception) {
                update { it.copy(conn = ConnState.FAILED, connMessage = errorMessage(e)) }
            }
        }
    }

    // MARK: - Time sync (Cristian's algorithm)

    fun syncTime(samples: Int = 5) {
        scope.launch {
            if (!isConfigured()) return@launch
            performTimeSync(samples)
        }
    }

    /** Run the Cristian sync loop, keeping the smallest-round-trip sample. */
    private suspend fun performTimeSync(samples: Int) {
        update { it.copy(lastError = null) }
        val cl = client()
        var best: Pair<Double, Double>? = null // offset, rtt
        repeat(maxOf(1, samples)) {
            val t0 = clock.nowUtc()
            try {
                val response = cl.time()
                val t1 = clock.nowUtc()
                val rtt = t1 - t0
                val edgeAtT1 = response.tUtc + rtt / 2
                val offset = edgeAtT1 - t1
                if (best == null || rtt < best!!.second) best = offset to rtt
            } catch (e: Exception) {
                update { it.copy(lastError = errorMessage(e)) }
                return
            }
        }
        best?.let { b ->
            update { it.copy(timeOffset = b.first, timeRoundTrip = b.second) }
            Log.i(AppInfo.TAG, "Time sync offset=${b.first}s rtt=${b.second}s")
        }
    }

    // MARK: - Investigation

    fun discover(sessionId: String) {
        scope.launch {
            if (!ensureConfigured()) return@launch
            update { it.copy(isBusy = true, lastError = null) }
            try {
                val cl = client()
                cl.createSession(sessionId, _state.value.timeOffset, AppInfo.CLOCK_SOURCE)
                cl.discover(mode.value)
                connect()
            } catch (e: Exception) {
                update { it.copy(lastError = errorMessage(e)) }
            } finally {
                update { it.copy(isBusy = false) }
            }
        }
    }

    // MARK: - Coordinated recording

    /**
     * Start the edge FIRST (`POST /api/session` with the shared id + offset, then
     * `POST /api/log/start`); only if that succeeds do we start the phone with the
     * SAME `session_id` — so we never record alone if the edge rejects the request.
     */
    fun startRecording(controller: RecordingController) {
        scope.launch {
            if (!ensureConfigured()) return@launch
            if (controller.status.value.isRecording) return@launch
            update { it.copy(isBusy = true, lastError = null) }
            val sessionId = controller.sessionId.value
            try {
                val cl = client()
                cl.createSession(sessionId, _state.value.timeOffset, AppInfo.CLOCK_SOURCE)
                cl.startLog()
            } catch (e: Exception) {
                update { it.copy(lastError = errorMessage(e), isBusy = false) }
                return@launch
            }
            controller.start() // phone side, same session_id
            connect()
            update { it.copy(isBusy = false) }
            Log.i(AppInfo.TAG, "Coordinated recording started for session $sessionId")
        }
    }

    fun stopRecording(controller: RecordingController) {
        scope.launch {
            update { it.copy(isBusy = true) }
            if (controller.status.value.isRecording) controller.stop()
            try {
                client().stopLog()
            } catch (e: Exception) {
                update { it.copy(lastError = errorMessage(e)) }
            }
            refreshStatus()
            update { it.copy(isBusy = false) }
        }
    }

    // MARK: - Live status (WebSocket + polling fallback)

    fun connect() {
        if (!isConfigured()) return
        disconnect()
        val cl = client()
        scope.launch { refreshStatus() }
        wsJob = scope.launch {
            try {
                cl.events().collect { event ->
                    update { it.copy(wsConnected = true) }
                    apply(event)
                }
            } catch (c: CancellationException) {
                throw c
            } catch (_: Exception) {
                // fall through to polling
            }
            ensureActive() // stop here if we were cancelled by disconnect()
            update { it.copy(wsConnected = false) }
            startPolling()
        }
    }

    fun disconnect() {
        wsJob?.cancel()
        wsJob = null
        pollJob?.cancel()
        pollJob = null
        update { it.copy(wsConnected = false) }
    }

    suspend fun refreshStatus() {
        if (!isConfigured()) return
        try {
            applyStatus(client().status())
        } catch (_: Exception) {
            // Non-fatal: keep last known state.
        }
    }

    private fun startPolling() {
        pollJob?.cancel()
        val cl = client()
        pollJob = scope.launch {
            while (isActive) {
                try {
                    applyStatus(cl.status())
                    update { it.copy(wsConnected = false) }
                } catch (_: Exception) {
                    // ignore transient errors while polling
                }
                delay(2000)
            }
        }
    }

    // MARK: - State application

    private fun apply(event: EdgeEvent) {
        when (event.event) {
            "state" -> event.state?.let { s -> update { it.copy(edgeState = s) } }
            "discovery" -> {} // progress phase — nothing to surface yet
            "discovery_done" -> event.summary?.let { s -> update { it.copy(discoverySummary = s) } }
            "stats" -> update {
                it.copy(
                    frames = event.frames ?: it.frames,
                    obdSamples = event.obdSamples ?: it.obdSamples,
                    elapsed = event.elapsedS ?: it.elapsed,
                )
            }
            "error" -> event.message?.let { m -> update { it.copy(lastError = m) } }
        }
    }

    private fun applyStatus(status: StatusResponse) {
        update {
            it.copy(
                edgeState = status.state,
                frames = status.stats?.frames ?: it.frames,
                obdSamples = status.stats?.obdSamples ?: it.obdSamples,
                elapsed = status.stats?.elapsedS ?: it.elapsed,
                discoverySummary = status.discoverySummary ?: it.discoverySummary,
                swVersion = status.device?.swVersion ?: it.swVersion,
                lastError = status.error ?: it.lastError,
                conn = if (it.conn != ConnState.CONNECTED) ConnState.CONNECTED else it.conn,
            )
        }
    }

    // MARK: - Helpers

    private fun ensureConfigured(): Boolean {
        if (isConfigured()) return true
        update { it.copy(lastError = "Enter the AutoPi host first") }
        return false
    }

    private fun errorMessage(error: Throwable): String =
        (error as? EdgeException)?.message ?: error.message ?: "Unknown error"

    private inline fun update(transform: (EdgeUiState) -> EdgeUiState) {
        _state.value = transform(_state.value)
    }

    companion object {
        private const val KEY_HOST = "edge.host"
        private const val KEY_TOKEN = "edge.token"
        private const val KEY_MODE = "edge.mode"
        private const val DEFAULT_HOST = "http://192.168.4.1:8765"
    }
}
