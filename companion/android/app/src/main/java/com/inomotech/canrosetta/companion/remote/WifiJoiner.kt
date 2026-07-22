package com.inomotech.canrosetta.companion.remote

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
import android.net.wifi.WifiNetworkSpecifier
import android.os.Build
import android.util.Log
import androidx.annotation.RequiresApi
import com.inomotech.canrosetta.companion.AppInfo
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.net.Inet4Address
import java.net.URI
import java.net.URISyntaxException
import javax.net.SocketFactory

/** Where the programmatic AP join currently stands (observed by the Pair screen). */
enum class JoinStatus { IDLE, UNSUPPORTED, REQUESTING, JOINED, FAILED }

data class WifiJoinState(val status: JoinStatus = JoinStatus.IDLE, val message: String? = null)

/**
 * Owns the programmatic join to the AutoPi's WPA2 access point, so the driver
 * never has to leave the app for Settings: [join] files a
 * [WifiNetworkSpecifier] request with the SSID + passphrase carried in the v2
 * pairing QR, and [socketFactoryFor] hands the resulting peer [Network]'s
 * socket factory to [EdgeControlClient] so control traffic for a host on that
 * network can be bound to it.
 *
 * Specifier requests exist only on API 29+ (Android 10). Below that we report
 * [JoinStatus.UNSUPPORTED] and the UI sends the user to Settings — exactly the
 * pre-v2 manual flow.
 */
class WifiJoiner(context: Context) {

    private val connectivity =
        context.applicationContext.getSystemService(ConnectivityManager::class.java)

    private val _state = MutableStateFlow(WifiJoinState())
    val state: StateFlow<WifiJoinState> = _state

    /**
     * The joined peer network, or null while not joined. Sockets must be created
     * through `network.socketFactory` to use it (see [EdgeControlClient]).
     */
    @Volatile
    var network: Network? = null
        private set

    private var callback: ConnectivityManager.NetworkCallback? = null

    /**
     * Request the AutoPi AP. Safe to call again (re-join): the old request is
     * released first, guarding against double registration. Progress lands on
     * [state]; the terminal outcomes are JOINED or FAILED.
     */
    fun join(ssid: String, psk: String) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            _state.value = WifiJoinState(
                JoinStatus.UNSUPPORTED,
                "Programmatic Wi-Fi join needs Android 10+",
            )
            return
        }
        // The specifier builder throws IllegalArgumentException on out-of-spec
        // input, and these values come straight from an untrusted QR — surface
        // FAILED with a readable message instead of crashing the app.
        credentialError(ssid, psk)?.let {
            _state.value = WifiJoinState(JoinStatus.FAILED, it)
            return
        }
        release()
        requestNetwork(ssid, psk)
    }

    /** Platform specifier rules: SSID 1..32 UTF-8 bytes, WPA2 passphrase 8..63 ASCII chars. */
    private fun credentialError(ssid: String, psk: String): String? = when {
        ssid.isEmpty() || ssid.toByteArray(Charsets.UTF_8).size > 32 ->
            "QR carried an invalid Wi-Fi name (must be 1–32 bytes)"
        psk.length !in 8..63 || psk.any { it.code !in 32..126 } ->
            "QR carried an invalid Wi-Fi password (WPA2 needs 8–63 ASCII characters)"
        else -> null
    }

    @RequiresApi(Build.VERSION_CODES.Q)
    private fun requestNetwork(ssid: String, psk: String) {
        // Built inside try/catch below: [credentialError] mirrors the platform's
        // documented rules, but the builder may still reject QR-supplied input —
        // untrusted data must degrade to FAILED, never crash.
        val request = try {
            val specifier = WifiNetworkSpecifier.Builder()
                .setSsid(ssid)
                .setWpa2Passphrase(psk)
                .build()
            // removeCapability(INTERNET) is mandatory for peer-to-peer specifier
            // requests — and it is also what makes them pleasant: Android never runs
            // internet validation on this network (no "no internet" nag, no
            // auto-disconnect), while the rest of the phone keeps its normal
            // connectivity for everything else.
            NetworkRequest.Builder()
                .addTransportType(NetworkCapabilities.TRANSPORT_WIFI)
                .removeCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .setNetworkSpecifier(specifier)
                .build()
        } catch (e: IllegalArgumentException) {
            _state.value = WifiJoinState(JoinStatus.FAILED, "Invalid Wi-Fi credentials: ${e.message}")
            return
        }
        val cb = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(net: Network) {
                network = net
                _state.value = WifiJoinState(JoinStatus.JOINED)
                Log.i(AppInfo.TAG, "Joined AutoPi AP '$ssid'")
            }

            override fun onUnavailable() {
                network = null
                _state.value = WifiJoinState(JoinStatus.FAILED, "user declined or AP not found")
            }

            override fun onLost(net: Network) {
                network = null
                _state.value = WifiJoinState(JoinStatus.IDLE, "Wi-Fi link lost")
            }
        }
        callback = cb
        _state.value = WifiJoinState(JoinStatus.REQUESTING)
        // After the user approves this exact-SSID request once, subsequent
        // identical requests auto-connect with NO dialog — the documented
        // low-friction repeat path we rely on for every drive after the first.
        try {
            connectivity.requestNetwork(request, cb)
        } catch (e: SecurityException) {
            // e.g. CHANGE_NETWORK_STATE revoked — fail visibly, never crash.
            callback = null
            _state.value = WifiJoinState(JoinStatus.FAILED, "Wi-Fi join not permitted: ${e.message}")
        }
    }

    /**
     * The joined network's `socketFactory`, but only when [hostUrl] actually
     * points into that network: its host must be a literal IPv4 inside one of
     * the network's link prefixes. Binding is required for the AutoPi's own
     * address (an app-scoped specifier network is never the default route),
     * yet binding a host that lives elsewhere — a dev box on the phone's
     * normal LAN — would make it unreachable. Null means default routing.
     */
    fun socketFactoryFor(hostUrl: String): SocketFactory? {
        val net = network ?: return null
        val host = try {
            URI(hostUrl.trim()).host
        } catch (_: URISyntaxException) {
            null
        } ?: return null
        val hostBytes = parseIpv4(host) ?: return null
        val link = connectivity.getLinkProperties(net) ?: return null
        val onLink = link.linkAddresses.any { la ->
            val peer = (la.address as? Inet4Address)?.address ?: return@any false
            samePrefix(hostBytes, peer, la.prefixLength)
        }
        return if (onLink) net.socketFactory else null
    }

    /** Strict dotted-quad parser — unlike InetAddress.getByName, never does DNS. */
    private fun parseIpv4(host: String): ByteArray? {
        val parts = host.split('.')
        if (parts.size != 4) return null
        val bytes = ByteArray(4)
        for (i in 0..3) {
            val p = parts[i]
            if (p.isEmpty() || p.length > 3 || !p.all { it.isDigit() }) return null
            val v = p.toInt()
            if (v > 255) return null
            bytes[i] = v.toByte()
        }
        return bytes
    }

    private fun samePrefix(a: ByteArray, b: ByteArray, prefixLength: Int): Boolean {
        if (b.size != a.size) return false
        val bits = prefixLength.coerceIn(0, a.size * 8)
        val fullBytes = bits / 8
        for (i in 0 until fullBytes) if (a[i] != b[i]) return false
        val rem = bits % 8
        if (rem == 0) return true
        val mask = (0xFF shl (8 - rem)) and 0xFF
        return (a[fullBytes].toInt() and mask) == (b[fullBytes].toInt() and mask)
    }

    /**
     * Drop the request and the network with it. A specifier network lives only
     * while its callback stays registered, so the joiner holds the registration
     * for the whole session and this is the one place it is torn down.
     */
    fun release() {
        callback?.let {
            try {
                connectivity.unregisterNetworkCallback(it)
            } catch (_: IllegalArgumentException) {
                // Already auto-released by the system (e.g. after onUnavailable).
            }
        }
        callback = null
        network = null
        _state.value = WifiJoinState(JoinStatus.IDLE)
    }
}
