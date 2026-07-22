package com.inomotech.canrosetta.companion.remote

import android.content.Context
import android.net.Network
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
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
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
 * Owns the phone side of the control channel: host/token/mode and the AutoPi's
 * AP credentials (persisted in SharedPreferences), the [WifiJoiner] that puts
 * the phone on that AP, the measured edge/companion clock offset, the live edge
 * status, and the coordinated start/stop that drives [RecordingController] in
 * lock-step with the AutoPi.
 *
 * The `session_id` is single-sourced from [RecordingController.sessionId] (the
 * phone mints it). The coordinated start sends that same id to the AutoPi via
 * `POST /api/session`, so both halves merge server-side.
 */
class EdgeConnection(context: Context) {

    private val appContext = context.applicationContext
    private val prefs = context.getSharedPreferences("edge", Context.MODE_PRIVATE)
    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())
    private val clock = Clock()

    val host = MutableStateFlow(prefs.getString(KEY_HOST, DEFAULT_HOST) ?: DEFAULT_HOST)
    val token = MutableStateFlow(prefs.getString(KEY_TOKEN, "") ?: "")
    val mode = MutableStateFlow(EdgeMode.fromWire(prefs.getString(KEY_MODE, "fast")))

    // AP credentials from the v2 pairing QR ("wifi" key). Empty when the payload
    // omitted them (dev boxes) — every Wi-Fi feature then degrades to today's
    // manual flow.
    val wifiSsid = MutableStateFlow(prefs.getString(KEY_WIFI_SSID, "") ?: "")
    val wifiPsk = MutableStateFlow(prefs.getString(KEY_WIFI_PSK, "") ?: "")

    // Lazy so construction (and its ConnectivityManager lookup) is deferred to
    // the first path that actually needs the joiner or its peer network. The
    // collector started alongside it lives for the whole session: a join
    // approved only after [joinAndPair]'s wait timed out, or an AP rejoin later
    // in the drive, would otherwise leave the app JOINED but never paired. One
    // handshake per transition to JOINED; CONNECTING/busy/in-flight all mean a
    // handshake is already running, so don't start a duplicate.
    private val joiner by lazy {
        WifiJoiner(appContext).also { j ->
            scope.launch {
                j.state.collect { join ->
                    if (join.status != JoinStatus.JOINED) return@collect
                    val s = _state.value
                    if (rePairInFlight || s.isBusy ||
                        s.conn == ConnState.CONNECTED || s.conn == ConnState.CONNECTING
                    ) return@collect
                    rePairInFlight = true
                    scope.launch {
                        try {
                            performPair() // includes the time sync on success
                        } finally {
                            rePairInFlight = false
                        }
                    }
                }
            }
        }
    }

    // Main-thread only (scope is Main.immediate): claims a JOINED transition so
    // the collector and joinAndPair() never run the handshake twice.
    private var rePairInFlight = false

    /** Join-progress of the programmatic AP join, for the Pair screen's Wi-Fi card. */
    val wifiJoin: StateFlow<WifiJoinState> get() = joiner.state

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

    fun setWifiSsid(value: String) {
        wifiSsid.value = value
        prefs.edit().putString(KEY_WIFI_SSID, value).apply()
    }

    fun setWifiPsk(value: String) {
        wifiPsk.value = value
        prefs.edit().putString(KEY_WIFI_PSK, value).apply()
    }

    fun isConfigured(): Boolean = host.value.trim().isNotEmpty()

    fun hasWifiCredentials(): Boolean = wifiSsid.value.isNotBlank() && wifiPsk.value.isNotBlank()

    /** True once the edge health check has succeeded (used by the drive flow). */
    fun isConnected(): Boolean = _state.value.conn == ConnState.CONNECTED

    // The joined peer network's socketFactory binds control traffic to the
    // AutoPi AP — without it, requests would take the phone's default
    // (internet) network and never reach 192.168.4.1. The joiner hands the
    // factory out only when the configured host actually lives on that network,
    // so a manually entered host elsewhere (dev box on the LAN) keeps default
    // routing. Cached per (host, token, bound network): bound clients carry a
    // private ConnectionPool (see EdgeControlClient), so rebuilding on every
    // call would forfeit keep-alive reuse within a network epoch.
    private var cachedClient: EdgeControlClient? = null
    private var cachedClientKey: Triple<String, String, Network?>? = null

    private fun client(): EdgeControlClient {
        val factory = joiner.socketFactoryFor(host.value)
        val key = Triple(host.value, token.value, if (factory != null) joiner.network else null)
        cachedClient?.let { if (cachedClientKey == key) return it }
        return EdgeControlClient(host.value, token.value, factory).also {
            cachedClient = it
            cachedClientKey = key
        }
    }

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
        scope.launch { performPair() }
    }

    /** The [pair] body, suspend so the joiner-state collector can track completion. */
    private suspend fun performPair() {
        if (!isConfigured()) {
            update { it.copy(conn = ConnState.FAILED, connMessage = "Enter the AutoPi host first") }
            return
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

    // MARK: - Wi-Fi join (v2 pairing payloads)

    /** Manual "Connect to AutoPi Wi-Fi" action from the Pair screen. */
    fun connectWifi() {
        if (!hasWifiCredentials()) return
        joiner.join(wifiSsid.value, wifiPsk.value)
    }

    /**
     * The one-tap connect path after scanning a v2 QR: join the AutoPi AP (when
     * the payload carried credentials), wait for the join to settle, then run
     * the normal [pair] + [syncTime] handshake over the peer network. With no
     * credentials, or on UNSUPPORTED / FAILED / timeout, we still fall through
     * to a plain [pair] — exactly today's behavior, and it also covers a phone
     * that already sits on the AP via a manual Settings join.
     */
    fun joinAndPair() {
        scope.launch {
            if (hasWifiCredentials()) {
                joiner.join(wifiSsid.value, wifiPsk.value)
                // The specifier dialog waits on the user, so give it a generous
                // window; REQUESTING is the only non-terminal state after join().
                withTimeoutOrNull(WIFI_JOIN_TIMEOUT_MS) {
                    joiner.state.first { it.status != JoinStatus.REQUESTING }
                }
                // The joiner-state collector may have claimed this JOINED
                // transition already (it subscribes first) — a second handshake
                // here would just race it.
                if (rePairInFlight) return@launch
            }
            pair()
            syncTime()
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
    fun startRecording(controller: RecordingController, enableCamera: Boolean = true) {
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
            controller.start(enableCamera) // phone side, same session_id
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
        private const val KEY_WIFI_SSID = "edge.wifi_ssid"
        private const val KEY_WIFI_PSK = "edge.wifi_psk"
        private const val DEFAULT_HOST = "http://192.168.4.1:8765"

        /** How long [joinAndPair] waits for the user to approve the join dialog. */
        private const val WIFI_JOIN_TIMEOUT_MS = 30_000L
    }
}
