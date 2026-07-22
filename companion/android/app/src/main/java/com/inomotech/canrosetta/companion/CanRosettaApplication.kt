package com.inomotech.canrosetta.companion

import android.app.Application
import androidx.lifecycle.ProcessLifecycleOwner
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.recording.RecordingForegroundService
import com.inomotech.canrosetta.companion.remote.EdgeConnection
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

/**
 * Process-wide owner of [RecordingController] and [EdgeConnection].
 *
 * The Android Auto [car.CanRosettaCarAppService] must reach the controller
 * without [MainActivity] existing (the phone may be locked in a pocket), so
 * both singletons live here instead of being created per-activity.
 *
 * [ProcessLifecycleOwner] is handed to the controller as its camera-binding
 * lifecycle: the controller uses the owner *only* for
 * `ProcessCameraProvider.bindToLifecycle`, and [ProcessLifecycleOwner] is
 * RESUMED exactly while any activity is resumed. Phone-UI recordings therefore
 * bind and run the camera exactly as before, while car-initiated recordings
 * (no activity in the foreground) simply leave the camera use cases dormant
 * and record IMU/GPS/car-data only — CameraX starts them automatically if the
 * driver later opens the phone app mid-session.
 *
 * This holder also ties [RecordingForegroundService] to the recording state so
 * a session survives the phone being locked regardless of which surface
 * (phone UI, car screen, edge coordination) started it.
 */
class CanRosettaApplication : Application() {

    lateinit var recordingController: RecordingController
        private set

    lateinit var edgeConnection: EdgeConnection
        private set

    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())

    private val _foregroundServiceBlocked = MutableStateFlow(false)

    /**
     * True while a recording runs WITHOUT its foreground service because the OS
     * refused the background start (Android 12+ restriction — see
     * [RecordingForegroundService]). The car screen surfaces this as a hint to
     * open the phone app once; recording itself continues as long as the
     * process lives.
     */
    val foregroundServiceBlocked: StateFlow<Boolean> = _foregroundServiceBlocked

    override fun onCreate() {
        super.onCreate()
        recordingController = RecordingController(this, ProcessLifecycleOwner.get())
        edgeConnection = EdgeConnection(this)

        // Start/stop the foreground service in lock-step with the recording
        // state. The initial `false` emission harmlessly stops a service that
        // was never started.
        scope.launch {
            recordingController.status
                .map { it.isRecording }
                .distinctUntilChanged()
                .collect { recording ->
                    if (recording) {
                        val started = RecordingForegroundService.start(this@CanRosettaApplication)
                        _foregroundServiceBlocked.value = !started
                    } else {
                        RecordingForegroundService.stop(this@CanRosettaApplication)
                        _foregroundServiceBlocked.value = false
                    }
                }
        }
    }

    /** Called by the service itself when `startForeground` is rejected in-flight. */
    fun noteForegroundServiceBlocked() {
        _foregroundServiceBlocked.value = true
    }
}
