package com.inomotech.canrosetta.companion.recording

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.SystemClock
import android.util.Log
import androidx.camera.core.CameraSelector
import androidx.camera.core.UseCase
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.io.JsonlWriter
import com.inomotech.canrosetta.companion.io.SessionManifest
import com.inomotech.canrosetta.companion.sensors.LocationSource
import com.inomotech.canrosetta.companion.sensors.MotionSource
import com.inomotech.canrosetta.companion.time.Clock
import com.inomotech.canrosetta.companion.time.SessionId
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.coroutines.resume

/** Live status snapshot the UI observes. */
data class RecordingStatus(
    val isRecording: Boolean = false,
    val motionCount: Long = 0,
    val locationCount: Long = 0,
    val videoFrameCount: Int = 0,
    val photoCount: Int = 0,
    val imuRateHz: Double = 0.0,
    val gpsHorizontalAccuracy: Double? = null,
    val locationPermissionGranted: Boolean = false,
    val elapsed: Double = 0.0,
    val exportPath: String? = null,
    val lastError: String? = null,
)

/**
 * Orchestrates one recording session: owns the sensor sources, writers, and the
 * (optional) camera use cases; creates the session directory; writes the manifest
 * and a shareable zip at stop. Publishes live counters via [status].
 *
 * Layout produced (under the app's files dir):
 * ```
 * sessions/session-<id>/
 * ├── manifest.json
 * └── phone/
 *     ├── motion.jsonl
 *     ├── location.jsonl
 *     ├── video.mp4            (only if "film dashboard" was on and bound)
 *     ├── video_index.jsonl
 *     ├── photos/NNNNNN.jpg    (only if "capture stills" was on and bound)
 *     └── photos_index.jsonl
 * ```
 */
class RecordingController(
    private val context: Context,
    private val lifecycleOwner: LifecycleOwner,
) {
    // Editable inputs the UI binds two-way.
    val sessionId = MutableStateFlow(SessionId.generate())
    val filmDashboard = MutableStateFlow(false)
    val capturePhotos = MutableStateFlow(true)
    val photoIntervalSeconds = 0.5

    private val _status = MutableStateFlow(RecordingStatus())
    val status: StateFlow<RecordingStatus> = _status

    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())

    // Dedicated executors kept for the controller's lifetime.
    private val analysisExecutor = Executors.newSingleThreadExecutor()
    private val photoIoExecutor = Executors.newSingleThreadExecutor()

    private var clock: Clock? = null
    private var motionSource: MotionSource? = null
    private var locationSource: LocationSource? = null
    private var videoRecorder: VideoRecorder? = null
    private var photoCapture: PhotoCapture? = null
    private var motionWriter: JsonlWriter? = null
    private var locationWriter: JsonlWriter? = null
    private var cameraProvider: ProcessCameraProvider? = null

    private var sessionDir: File? = null
    private var startUtc: Double = 0.0

    private var tickerJob: Job? = null
    private var lastRateCount = 0L
    private var lastRateTimeMs = 0L

    // MARK: - Public control

    fun setSessionId(value: String) {
        if (_status.value.isRecording) return
        sessionId.value = value
    }

    fun newSessionId() {
        if (_status.value.isRecording) return
        sessionId.value = SessionId.generate()
    }

    fun setFilmDashboard(value: Boolean) {
        if (_status.value.isRecording) return
        filmDashboard.value = value
    }

    fun setCapturePhotos(value: Boolean) {
        if (_status.value.isRecording) return
        capturePhotos.value = value
    }

    fun setLocationPermissionGranted(granted: Boolean) {
        update { it.copy(locationPermissionGranted = granted) }
    }

    fun start() {
        if (_status.value.isRecording) return
        update { it.copy(exportPath = null, lastError = null) }

        try {
            val clock = Clock()
            this.clock = clock
            startUtc = clock.nowUtc()

            val dir = makeSessionDir(sessionId.value)
            sessionDir = dir
            val phone = File(dir, "phone").apply { mkdirs() }

            val motionWriter = JsonlWriter(File(phone, "motion.jsonl"))
            val locationWriter = JsonlWriter(File(phone, "location.jsonl"))
            this.motionWriter = motionWriter
            this.locationWriter = locationWriter

            val motion = MotionSource(context, clock)
            motion.onRecord = { motionWriter.append(it) }
            motionSource = motion
            motion.start()

            val location = LocationSource(context)
            location.onRecord = { locationWriter.append(it) }
            locationSource = location
            location.start()

            if (filmDashboard.value || capturePhotos.value) {
                if (hasCameraPermission()) {
                    startCamera(clock, phone)
                } else {
                    update { it.copy(lastError = "Camera permission not granted; recording IMU/GPS only") }
                }
            }

            update { it.copy(isRecording = true) }
            startTicker()
            Log.i(AppInfo.TAG, "Recording started for session ${sessionId.value}")
        } catch (e: Exception) {
            Log.e(AppInfo.TAG, "Failed to start recording: ${e.message}")
            update { it.copy(lastError = e.message) }
            cleanupAfterFailure()
        }
    }

    fun stop() {
        if (!_status.value.isRecording) return
        update { it.copy(isRecording = false) }
        stopTicker()

        motionSource?.stop()
        locationSource?.stop()
        photoCapture?.stop()

        val motionRows = motionWriter?.rowCount ?: 0
        val locationRows = locationWriter?.rowCount ?: 0
        val hadVideo = videoRecorder != null
        val start = startUtc
        val endUtc = clock?.nowUtc() ?: (System.currentTimeMillis() / 1000.0)

        scope.launch {
            val video = videoRecorder
            if (video != null) {
                suspendCancellableCoroutine<Unit> { cont ->
                    video.stop { cont.resume(Unit) }
                }
            }
            cameraProvider?.unbindAll()
            motionWriter?.close()
            locationWriter?.close()

            val videoFrames = videoRecorder?.frameCount ?: 0
            try {
                writeManifest(motionRows, locationRows, hadVideo, videoFrames, start, endUtc)
                val zip = withContext(Dispatchers.IO) { exportArchive() }
                update { it.copy(exportPath = zip.absolutePath) }
                Log.i(AppInfo.TAG, "Session finalised: ${zip.name}")
            } catch (e: Exception) {
                Log.e(AppInfo.TAG, "Failed to finalise session: ${e.message}")
                update { it.copy(lastError = e.message) }
            }

            motionSource = null
            videoRecorder = null
            photoCapture = null
            motionWriter = null
            locationWriter = null
            cameraProvider = null
            clock = null
        }
    }

    // MARK: - Camera

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED

    private fun startCamera(clock: Clock, phoneDir: File) {
        val future = ProcessCameraProvider.getInstance(context)
        future.addListener({
            try {
                bindCamera(future.get(), clock, phoneDir)
            } catch (e: Exception) {
                Log.e(AppInfo.TAG, "Camera provider unavailable: ${e.message}")
                update { it.copy(lastError = "Camera unavailable: ${e.message}") }
            }
        }, ContextCompat.getMainExecutor(context))
    }

    private fun bindCamera(provider: ProcessCameraProvider, clock: Clock, phoneDir: File) {
        cameraProvider = provider
        val selector = CameraSelector.DEFAULT_BACK_CAMERA

        // Priority order (most to least important): video, its index, stills.
        // Fallback drops from the tail if the device cannot bind the full set.
        val useCases = mutableListOf<UseCase>()

        var video: VideoRecorder? = null
        if (filmDashboard.value) {
            video = VideoRecorder(
                context, clock,
                File(phoneDir, "video.mp4"),
                File(phoneDir, "video_index.jsonl"),
                analysisExecutor,
            )
            useCases.add(video.videoCapture)
            useCases.add(video.imageAnalysis)
        }

        var photo: PhotoCapture? = null
        if (capturePhotos.value) {
            photo = PhotoCapture(
                clock,
                File(phoneDir, "photos"),
                File(phoneDir, "photos_index.jsonl"),
                (photoIntervalSeconds * 1000).toLong(),
                photoIoExecutor,
            )
            useCases.add(photo.imageCapture)
        }

        val bound = bindWithFallback(provider, selector, useCases)

        if (video != null) {
            if (bound.contains(video.videoCapture)) {
                video.start()
                videoRecorder = video
            } else {
                video.cancel()
                update { it.copy(lastError = "Video could not be bound to the camera") }
            }
        }

        if (photo != null) {
            if (bound.contains(photo.imageCapture)) {
                photo.start()
                photoCapture = photo
            } else {
                photo.cancel()
                update { it.copy(lastError = "Stills disabled: camera cannot bind video and stills together") }
            }
        }
    }

    /**
     * Bind [useCases]; on failure, drop the last (least critical) and retry until
     * something binds or the list is empty. Returns the use cases actually bound.
     */
    private fun bindWithFallback(
        provider: ProcessCameraProvider,
        selector: CameraSelector,
        useCases: List<UseCase>,
    ): List<UseCase> {
        val attempt = useCases.toMutableList()
        while (attempt.isNotEmpty()) {
            try {
                provider.unbindAll()
                provider.bindToLifecycle(lifecycleOwner, selector, *attempt.toTypedArray())
                return attempt.toList()
            } catch (e: Exception) {
                Log.w(AppInfo.TAG, "Camera bind failed with ${attempt.size} use case(s): ${e.message}")
                attempt.removeAt(attempt.size - 1)
            }
        }
        provider.unbindAll()
        return emptyList()
    }

    // MARK: - Manifest & export

    private fun writeManifest(
        motionRows: Long,
        locationRows: Long,
        hadVideo: Boolean,
        videoFrames: Int,
        startUtc: Double,
        endUtc: Double,
    ) {
        val dir = sessionDir ?: return
        val streams = mutableListOf(
            SessionManifest.Stream("phone/motion.jsonl", "motion", motionRows, null, startUtc, endUtc),
            SessionManifest.Stream("phone/location.jsonl", "location", locationRows, null, startUtc, endUtc),
        )
        if (hadVideo && videoFrames > 0) {
            streams.add(
                SessionManifest.Stream(
                    "phone/video.mp4", "video", videoFrames.toLong(),
                    "phone/video_index.jsonl", startUtc, endUtc,
                )
            )
        }
        val manifest = SessionManifest.build(
            sessionId = sessionId.value,
            createdUtc = startUtc,
            deviceId = SessionManifest.deviceId(context),
            clockSource = AppInfo.CLOCK_SOURCE,
            utcOffsetEstS = 0.0,
            errEstS = 0.1,
            streams = streams,
        )
        SessionManifest.write(File(dir, "manifest.json"), manifest)
    }

    /** Zip the whole `session-<id>/` directory into a shareable archive. */
    private fun exportArchive(): File {
        val dir = sessionDir ?: throw IllegalStateException("No session directory")
        val base = dir.parentFile ?: throw IllegalStateException("No parent directory")
        val exportsDir = File(context.cacheDir, "exports").apply { mkdirs() }
        val zipFile = File(exportsDir, "session-${sessionId.value}.zip")
        if (zipFile.exists()) zipFile.delete()

        ZipOutputStream(BufferedOutputStream(FileOutputStream(zipFile))).use { zos ->
            dir.walkTopDown().filter { it.isFile }.forEach { file ->
                val entryName = file.relativeTo(base).path
                zos.putNextEntry(ZipEntry(entryName))
                file.inputStream().use { it.copyTo(zos) }
                zos.closeEntry()
            }
        }
        return zipFile
    }

    private fun makeSessionDir(id: String): File {
        val dir = File(File(context.filesDir, "sessions"), "session-$id")
        File(dir, "phone").mkdirs()
        return dir
    }

    // MARK: - Live UI ticker

    private fun startTicker() {
        lastRateCount = 0
        lastRateTimeMs = SystemClock.elapsedRealtime()
        tickerJob = scope.launch {
            while (isActive) {
                tick()
                delay(250)
            }
        }
    }

    private fun stopTicker() {
        tickerJob?.cancel()
        tickerJob = null
    }

    private fun tick() {
        val c = clock
        val elapsed = if (c != null) c.nowUtc() - startUtc else 0.0
        val mCount = motionWriter?.rowCount ?: 0
        val nowMs = SystemClock.elapsedRealtime()
        val dt = (nowMs - lastRateTimeMs) / 1000.0
        var rate = _status.value.imuRateHz
        if (dt >= 0.5) {
            rate = (mCount - lastRateCount) / dt
            lastRateCount = mCount
            lastRateTimeMs = nowMs
        }
        update {
            it.copy(
                elapsed = elapsed,
                motionCount = mCount,
                locationCount = locationWriter?.rowCount ?: 0,
                videoFrameCount = videoRecorder?.frameCount ?: 0,
                photoCount = photoCapture?.photoCount ?: 0,
                imuRateHz = rate,
                gpsHorizontalAccuracy = locationSource?.lastHorizontalAccuracy,
            )
        }
    }

    private fun cleanupAfterFailure() {
        motionSource?.stop()
        locationSource?.stop()
        photoCapture?.stop()
        videoRecorder?.cancel()
        motionWriter?.close()
        locationWriter?.close()
        motionSource = null
        photoCapture = null
        videoRecorder = null
        motionWriter = null
        locationWriter = null
        stopTicker()
        update { it.copy(isRecording = false) }
    }

    private inline fun update(transform: (RecordingStatus) -> RecordingStatus) {
        _status.value = transform(_status.value)
    }
}
