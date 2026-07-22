package com.inomotech.canrosetta.companion.recording

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.os.Handler
import android.os.HandlerThread
import android.os.StatFs
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
import com.inomotech.canrosetta.companion.time.TimeMath
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
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.util.concurrent.Executors
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream
import kotlin.coroutines.resume
import kotlin.math.sqrt

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
    /** Cumulative ground distance from GPS fixes (metres). */
    val distanceMeters: Double = 0.0,
    val exportPath: String? = null,
    val lastError: String? = null,
)

/**
 * Latest, lightly-smoothed user acceleration in g (gravity removed), sampled from
 * the IMU at ~30 Hz for the recording screen's g-ball. `x` = lateral (device
 * right +), `y` = longitudinal (device up +). Mirrors iOS `accelGX/accelGY`.
 */
data class AccelG(val x: Double = 0.0, val y: Double = 0.0)

/**
 * Pre-flight mount-steadiness estimate. `rms` is the rolling standard deviation of
 * accelerometer magnitude (g) from the standby vibration monitor — a proxy for how
 * much the cradle rattles. Mirrors iOS `mountVibrationRMS`/`hasMountData`.
 */
data class MountState(val rms: Double = 0.0, val hasData: Boolean = false) {
    /** Steady enough to record — or no accelerometer data at all (e.g. emulator). */
    val steady: Boolean get() = !hasData || rms < MOUNT_VIBRATION_THRESHOLD

    companion object {
        /** The standby monitor treats the cradle as steady below this g-RMS. */
        const val MOUNT_VIBRATION_THRESHOLD = 0.08
    }
}

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
 *     ├── car_hw.jsonl         (only if an Android Auto car session fed records)
 *     ├── video.mp4            (only if "film dashboard" was on and bound)
 *     ├── video_index.jsonl
 *     ├── photos/NNNNNN.jpg    (only if "capture stills" was on and bound)
 *     └── photos_index.jsonl
 * ```
 */
class RecordingController(
    private val context: Context,
    /**
     * Used ONLY for CameraX `bindToLifecycle`. The process-wide holder
     * (`CanRosettaApplication`) passes `ProcessLifecycleOwner`, which is
     * RESUMED while any activity is — so phone-UI camera recording behaves as
     * before, and car-initiated sessions simply run without the camera.
     */
    private val lifecycleOwner: LifecycleOwner,
) {
    // Editable inputs the UI binds two-way.
    val sessionId = MutableStateFlow(SessionId.generate())
    val filmDashboard = MutableStateFlow(false)
    val capturePhotos = MutableStateFlow(true)
    val photoIntervalSeconds = 0.5

    private val _status = MutableStateFlow(RecordingStatus())
    val status: StateFlow<RecordingStatus> = _status

    /** Live g-ball feed (own flow, written from the sensor thread — no race with [status]). */
    private val _accel = MutableStateFlow(AccelG())
    val accel: StateFlow<AccelG> = _accel

    /** Pre-flight mount steadiness (own flow, written from the vibration thread). */
    private val _mount = MutableStateFlow(MountState())
    val mount: StateFlow<MountState> = _mount

    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())

    private val sensorManager by lazy {
        context.getSystemService(Context.SENSOR_SERVICE) as SensorManager
    }

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

    // Companion car-hardware reference stream (phone/car_hw.jsonl), fed by
    // car.CarHardwareLogger while an Android Auto session is alive. Created
    // lazily on first append so drives without a head unit don't ship an
    // empty stream.
    private var carHwWriter: JsonlWriter? = null

    // A standby GPS source so pre-flight can surface a live fix before recording.
    private var standbyLocation: LocationSource? = null

    // Pre-flight standby vibration monitor (independent of the recording IMU).
    private var vibThread: HandlerThread? = null
    private var vibListener: SensorEventListener? = null
    private val vibMagnitudes = ArrayDeque<Double>() // touched only on the vib thread

    private var sessionDir: File? = null
    private var startUtc: Double = 0.0

    // Distance accumulation from GPS fixes (touched only on the main looper).
    private var distanceMeters = 0.0
    private var lastFixLat: Double? = null
    private var lastFixLon: Double? = null

    // Sync markers pinned into this session (written into the manifest at stop, and
    // re-persisted if a marker is pinned just after stopping).
    private val syncMarkers = mutableListOf<SessionManifest.SyncMarker>()

    // Cached finalize inputs so a post-stop sync marker can re-write manifest/zip.
    private var finalizeStreams: List<SessionManifest.Stream> = emptyList()
    private var finalizeCreatedUtc: Double = 0.0

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

    // MARK: - Pre-flight monitors

    /**
     * Start the live checks the pre-flight screen relies on: begin a standby GPS
     * fix (if permitted) to surface accuracy, and monitor accelerometer vibration
     * to judge how firmly the phone is cradled. Idempotent.
     */
    fun startPreflight() {
        if (!_status.value.isRecording && standbyLocation == null &&
            _status.value.locationPermissionGranted && hasLocationPermission()
        ) {
            val loc = LocationSource(context)
            loc.onRecord = { rec ->
                val h = rec.optDouble("h_acc", -1.0)
                if (h >= 0) update { it.copy(gpsHorizontalAccuracy = h) }
            }
            loc.start()
            standbyLocation = loc
        }
        startVibrationMonitor()
    }

    /**
     * Stop the pre-flight vibration monitor (called when leaving pre-flight without
     * recording). The standby GPS is left running; it is cheap and warms up the fix
     * for the drive.
     */
    fun stopPreflight() {
        stopVibrationMonitor()
    }

    /**
     * Begin recording. [enableCamera] gates the camera entirely: car-initiated
     * sessions pass `false` because the car surface means a docked/pocketed
     * phone, and with the camera bound to `ProcessLifecycleOwner` it would
     * otherwise power on the moment the driver opens the phone app mid-drive.
     * Phone-UI sessions keep the default `true`.
     */
    fun start(enableCamera: Boolean = true) {
        if (_status.value.isRecording) return
        update { it.copy(exportPath = null, lastError = null, distanceMeters = 0.0) }

        // The recording sources take over from the standby monitors.
        standbyLocation?.stop()
        standbyLocation = null
        stopVibrationMonitor()

        syncMarkers.clear()
        distanceMeters = 0.0
        lastFixLat = null
        lastFixLon = null
        _accel.value = AccelG()

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
            // Persist every sample; forward a throttled, low-passed copy to the
            // g-ball. This lambda runs only on the motion handler thread, so the
            // captured throttle/low-pass state needs no extra synchronisation.
            var lastBallMs = 0L
            var ballX = 0.0
            var ballY = 0.0
            motion.onRecord = { rec ->
                motionWriter.append(rec)
                val nowMs = SystemClock.elapsedRealtime()
                if (nowMs - lastBallMs >= 33L) {
                    lastBallMs = nowMs
                    val acc = rec.optJSONArray("acc")
                    if (acc != null && acc.length() >= 2) {
                        val ax = acc.optDouble(0, 0.0)
                        val ay = acc.optDouble(1, 0.0)
                        val a = 0.35
                        ballX = ballX * (1 - a) + ax * a
                        ballY = ballY * (1 - a) + ay * a
                        _accel.value = AccelG(ballX, ballY)
                    }
                }
            }
            motionSource = motion
            motion.start()

            val location = LocationSource(context)
            location.onRecord = { rec ->
                locationWriter.append(rec)
                val h = rec.optDouble("h_acc", -1.0)
                if (h >= 0) accumulateDistance(rec.optDouble("lat"), rec.optDouble("lon"))
            }
            locationSource = location
            location.start()

            if (enableCamera && (filmDashboard.value || capturePhotos.value)) {
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
        _accel.value = AccelG()

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
            carHwWriter?.close()

            val videoFrames = videoRecorder?.frameCount ?: 0
            // The car_hw writer only exists if an Android Auto session delivered
            // records this drive; its count is final because appends are gated on
            // isRecording, which flipped before this coroutine ran.
            val carHwRows = carHwWriter?.rowCount ?: 0
            finalizeStreams =
                buildStreams(motionRows, locationRows, carHwRows, hadVideo, videoFrames, start, endUtc)
            finalizeCreatedUtc = start
            rewriteManifestAndExport()

            // Reset transient owners. Keep sessionDir + syncMarkers so a post-stop
            // sync marker can stamp and re-persist.
            motionSource = null
            videoRecorder = null
            photoCapture = null
            motionWriter = null
            locationWriter = null
            carHwWriter = null
            cameraProvider = null
            clock = null
        }
    }

    // MARK: - Sync markers

    /**
     * Pin a sync marker (e.g. a triple brake-flash) into the current session. If
     * recording, it lands in the manifest at stop; if pinned just after stop (the
     * guided "sync marker" step), the manifest and archive are re-written. Mirrors
     * iOS `addSyncMarker`.
     */
    fun addSyncMarker(kind: String, count: Int? = null) {
        val t = clock?.nowUtc() ?: (System.currentTimeMillis() / 1000.0)
        synchronized(syncMarkers) {
            syncMarkers.add(SessionManifest.SyncMarker(kind, t, count))
        }
        Log.i(AppInfo.TAG, "Pinned sync marker $kind at $t")
        if (!_status.value.isRecording && sessionDir != null) {
            scope.launch { rewriteManifestAndExport() }
        }
    }

    // MARK: - Car hardware reference stream

    /**
     * Append one `phone/car_hw.jsonl` record (built by `car.CarHwRecords`,
     * delivered by `car.CarHardwareLogger`). Accepted ONLY while a recording
     * session is active — the stream lives and dies with the session, exactly
     * like motion/location. Called on the main thread (the logger's callbacks
     * run on the main executor), the same thread [start]/[stop] mutate the
     * session state on, so no extra synchronisation is needed; a record racing
     * a just-finished [stop] is dropped by the writer's closed flag.
     */
    fun appendCarHardware(record: JSONObject) {
        if (!_status.value.isRecording) return
        val dir = sessionDir ?: return
        val writer = carHwWriter
            ?: JsonlWriter(File(File(dir, "phone"), "car_hw.jsonl")).also { carHwWriter = it }
        writer.append(record)
    }

    // MARK: - Camera

    private fun hasCameraPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED

    private fun hasLocationPermission(): Boolean =
        ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) ==
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

    private fun buildStreams(
        motionRows: Long,
        locationRows: Long,
        carHwRows: Long,
        hadVideo: Boolean,
        videoFrames: Int,
        startUtc: Double,
        endUtc: Double,
    ): List<SessionManifest.Stream> {
        val streams = mutableListOf(
            SessionManifest.Stream("phone/motion.jsonl", "motion", motionRows, null, startUtc, endUtc),
            SessionManifest.Stream("phone/location.jsonl", "location", locationRows, null, startUtc, endUtc),
        )
        // Registered exactly like motion/location, but only when the drive had
        // an Android Auto session feeding records (the file is created lazily).
        if (carHwRows > 0) {
            streams.add(
                SessionManifest.Stream("phone/car_hw.jsonl", "car_hw", carHwRows, null, startUtc, endUtc)
            )
        }
        if (hadVideo && videoFrames > 0) {
            streams.add(
                SessionManifest.Stream(
                    "phone/video.mp4", "video", videoFrames.toLong(),
                    "phone/video_index.jsonl", startUtc, endUtc,
                )
            )
        }
        return streams
    }

    /**
     * Write `manifest.json` (with the current sync markers) and re-export the zip
     * archive. Safe to call again after stop when a marker is pinned.
     */
    private suspend fun rewriteManifestAndExport() {
        val dir = sessionDir ?: return
        try {
            writeManifestFile(dir)
            val zip = withContext(Dispatchers.IO) { exportArchive() }
            update { it.copy(exportPath = zip.absolutePath) }
            Log.i(AppInfo.TAG, "Session finalised: ${zip.name}")
        } catch (e: Exception) {
            Log.e(AppInfo.TAG, "Failed to finalise session: ${e.message}")
            update { it.copy(lastError = e.message) }
        }
    }

    private fun writeManifestFile(dir: File) {
        val markersCopy = synchronized(syncMarkers) { syncMarkers.toList() }
        val manifest = SessionManifest.build(
            sessionId = sessionId.value,
            createdUtc = finalizeCreatedUtc,
            deviceId = SessionManifest.deviceId(context),
            clockSource = AppInfo.CLOCK_SOURCE,
            utcOffsetEstS = 0.0,
            errEstS = 0.1,
            streams = finalizeStreams,
            syncMarkers = markersCopy,
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

    // MARK: - Device capability queries

    /** Whether the recording IMU (linear acceleration) is available on this device. */
    val isMotionAvailable: Boolean
        get() = sensorManager.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION) != null

    /** Free disk available under the app's files dir, in bytes (via [StatFs]). */
    fun freeDiskBytes(): Long =
        try {
            StatFs(context.filesDir.absolutePath).availableBytes
        } catch (e: Exception) {
            Log.w(AppInfo.TAG, "StatFs failed: ${e.message}")
            Long.MAX_VALUE
        }

    // MARK: - Vibration monitor

    private fun startVibrationMonitor() {
        val accel = sensorManager.getDefaultSensor(Sensor.TYPE_ACCELEROMETER) ?: return
        if (vibThread != null) return
        synchronized(vibMagnitudes) { vibMagnitudes.clear() }
        _mount.value = MountState()
        val t = HandlerThread("canrosetta-vib").apply { start() }
        vibThread = t
        val handler = Handler(t.looper)
        val listener = object : SensorEventListener {
            override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
            override fun onSensorChanged(event: SensorEvent) {
                val x = TimeMath.toG(event.values[0])
                val y = TimeMath.toG(event.values[1])
                val z = TimeMath.toG(event.values[2])
                pushVibration(sqrt(x * x + y * y + z * z))
            }
        }
        vibListener = listener
        // ~20 Hz standby monitor.
        sensorManager.registerListener(listener, accel, 50_000, handler)
    }

    private fun stopVibrationMonitor() {
        vibListener?.let { sensorManager.unregisterListener(it) }
        vibListener = null
        vibThread?.quitSafely()
        vibThread = null
        synchronized(vibMagnitudes) { vibMagnitudes.clear() }
        _mount.value = MountState()
    }

    /** Rolling standard deviation of accelerometer magnitude over ~2 s (40 samples). */
    private fun pushVibration(mag: Double) {
        val snapshot = synchronized(vibMagnitudes) {
            vibMagnitudes.addLast(mag)
            while (vibMagnitudes.size > 40) vibMagnitudes.removeFirst()
            vibMagnitudes.toDoubleArray()
        }
        if (snapshot.size < 10) return
        val n = snapshot.size.toDouble()
        val mean = snapshot.sum() / n
        var variance = 0.0
        for (v in snapshot) variance += (v - mean) * (v - mean)
        variance /= n
        _mount.value = MountState(sqrt(variance), true)
    }

    private fun accumulateDistance(lat: Double, lon: Double) {
        val prevLat = lastFixLat
        val prevLon = lastFixLon
        if (prevLat != null && prevLon != null) {
            val results = FloatArray(1)
            Location.distanceBetween(prevLat, prevLon, lat, lon, results)
            val step = results[0].toDouble()
            if (step.isFinite()) distanceMeters += step
        }
        lastFixLat = lat
        lastFixLon = lon
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
                distanceMeters = distanceMeters,
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
        carHwWriter?.close()
        motionSource = null
        photoCapture = null
        videoRecorder = null
        motionWriter = null
        locationWriter = null
        carHwWriter = null
        stopTicker()
        _accel.value = AccelG()
        update { it.copy(isRecording = false) }
    }

    private inline fun update(transform: (RecordingStatus) -> RecordingStatus) {
        _status.value = transform(_status.value)
    }
}
