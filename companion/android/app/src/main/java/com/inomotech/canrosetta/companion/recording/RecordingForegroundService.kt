package com.inomotech.canrosetta.companion.recording

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.CanRosettaApplication
import com.inomotech.canrosetta.companion.MainActivity
import com.inomotech.canrosetta.companion.R
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.conflate
import kotlinx.coroutines.launch
import java.util.Locale

/**
 * Minimal foreground service that runs while a recording session is active —
 * the whole point of the car screen is recording with the phone locked in a
 * pocket, and without a FGS the OS is free to kill the process mid-drive.
 *
 * Started/stopped by [CanRosettaApplication] in lock-step with
 * `RecordingController.status.isRecording`; it owns no recording logic, only
 * the process-priority claim and a low-importance notification with live
 * session stats.
 *
 * Android 12+ background-start nuance: when recording is started from the car
 * screen, no activity of ours is in the foreground. The process IS bound by
 * the (foreground) Android Auto host, which in practice usually lifts us above
 * the background-start restriction — but that is host behaviour, not a
 * documented guarantee. So we ATTEMPT the start and degrade gracefully on
 * `ForegroundServiceStartNotAllowedException`: recording continues without the
 * FGS (the process singletons keep working while the process lives) and the
 * car screen shows a one-line hint to open the phone app once, which makes the
 * app foreground and lets the next start succeed.
 */
class RecordingForegroundService : Service() {

    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())
    private var updateJob: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val app = application as CanRosettaApplication
        val controller = app.recordingController
        createChannel()

        try {
            ServiceCompat.startForeground(
                this, NOTIFICATION_ID, buildNotification(controller.status.value), serviceType())
        } catch (e: IllegalStateException) {
            // ForegroundServiceStartNotAllowedException (S+) subclasses
            // IllegalStateException; catching the parent keeps minSdk 26 happy.
            Log.w(AppInfo.TAG, "Foreground start rejected (background start?): ${e.message}")
            app.noteForegroundServiceBlocked()
            stopSelf()
            return START_NOT_STICKY
        } catch (e: SecurityException) {
            // API 34 rejects the `location` type without an eligible location
            // grant/state — same graceful degradation.
            Log.w(AppInfo.TAG, "Foreground start rejected (type/permission): ${e.message}")
            app.noteForegroundServiceBlocked()
            stopSelf()
            return START_NOT_STICKY
        }

        // Refresh the stats line every couple of seconds; StateFlow is already
        // conflated, so sleeping in the collector naturally drops intermediate
        // states, and only-alert-once keeps the channel silent.
        updateJob?.cancel()
        updateJob = scope.launch {
            controller.status.collect { status ->
                notificationManager().notify(NOTIFICATION_ID, buildNotification(status))
                delay(2000)
            }
        }

        // If the OS kills us, a restart without the live controller state would
        // just show a stale notification — the app holder re-starts us anyway
        // when recording is (still) active.
        return START_NOT_STICKY
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    // MARK: - Notification

    private fun buildNotification(status: RecordingStatus): Notification {
        val openApp = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setContentTitle("Recording drive session")
            .setContentText(
                String.format(
                    Locale.US, "%s · %,d IMU · %,d GPS",
                    formatElapsed(status.elapsed), status.motionCount, status.locationCount,
                )
            )
            .setContentIntent(openApp)
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .build()
    }

    private fun createChannel() {
        // Low importance: a status chip, never a sound or heads-up.
        val channel = NotificationChannel(
            CHANNEL_ID, "Recording session", NotificationManager.IMPORTANCE_LOW)
        notificationManager().createNotificationChannel(channel)
    }

    private fun notificationManager(): NotificationManager =
        getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    /**
     * `location` only when the while-in-use grant exists (API 34 enforces the
     * pairing); `dataSync` covers the IMU/car-data capture either way.
     */
    private fun serviceType(): Int {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return 0 // types are ignored pre-Q
        var type = ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
        val hasLocation = ContextCompat.checkSelfPermission(
            this, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        if (hasLocation) type = type or ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION
        return type
    }

    private fun formatElapsed(seconds: Double): String {
        val total = seconds.toLong().coerceAtLeast(0)
        val h = total / 3600
        val m = (total % 3600) / 60
        val s = total % 60
        return if (h > 0) {
            String.format(Locale.US, "%d:%02d:%02d", h, m, s)
        } else {
            String.format(Locale.US, "%02d:%02d", m, s)
        }
    }

    companion object {
        private const val CHANNEL_ID = "recording"
        private const val NOTIFICATION_ID = 1

        /**
         * Attempt to start the service; false when the OS refuses (Android 12+
         * background-start restriction). The caller records the refusal so the
         * car screen can surface it — recording itself proceeds either way.
         */
        fun start(context: Context): Boolean = try {
            ContextCompat.startForegroundService(
                context, Intent(context, RecordingForegroundService::class.java))
            true
        } catch (e: IllegalStateException) {
            // ForegroundServiceStartNotAllowedException (S+), see class doc.
            Log.w(AppInfo.TAG, "startForegroundService rejected: ${e.message}")
            false
        } catch (e: SecurityException) {
            Log.w(AppInfo.TAG, "startForegroundService rejected: ${e.message}")
            false
        }

        fun stop(context: Context) {
            context.stopService(Intent(context, RecordingForegroundService::class.java))
        }
    }
}
