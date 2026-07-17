package com.inomotech.canrosetta.companion.recording

import android.util.Log
import androidx.camera.core.ImageCapture
import androidx.camera.core.ImageCaptureException
import androidx.camera.core.ImageProxy
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.io.JsonlWriter
import com.inomotech.canrosetta.companion.io.Records
import com.inomotech.canrosetta.companion.time.Clock
import java.io.File
import java.util.concurrent.Executor
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * Periodic full-resolution still capture of the dashboard to `phone/photos/`,
 * indexed by `phone/photos_index.jsonl`.
 *
 * ## Why stills *and* video?
 *
 * Video is temporally dense but compressed and low-resolution — great for a
 * turn-signal blink, poor for OCR of small digits. So we also fire a
 * full-resolution JPEG on a timer. The server routes numeric/gear OCR to the
 * nearest still and telltales/needles to the video.
 *
 * ## Coexistence with video
 *
 * The [imageCapture] use case is bound to the **same** CameraX lifecycle as the
 * video's `VideoCapture` (+ `ImageAnalysis`) by [RecordingController], so filming
 * and stills run off one camera. If the device cannot bind all use cases, the
 * controller drops this one and disables stills (graceful degradation).
 *
 * ## Timestamps
 *
 * We capture with [ImageCapture.OnImageCapturedCallback], reading the real
 * capture time from `ImageInfo.timestamp` (same domain as the video/IMU
 * timestamps) via the shared [Clock], and save the JPEG bytes ourselves so we can
 * record the exact pixel `w`/`h`.
 */
class PhotoCapture(
    private val clock: Clock,
    private val photosDir: File,
    indexFile: File,
    private val intervalMs: Long,
    private val ioExecutor: Executor,
) {

    val imageCapture: ImageCapture = ImageCapture.Builder()
        .setCaptureMode(ImageCapture.CAPTURE_MODE_MAXIMIZE_QUALITY)
        .build()

    private val indexWriter = JsonlWriter(indexFile)
    private val savedCounter = AtomicInteger(0)
    private val inFlight = AtomicInteger(0)
    private val maxInFlight = 4
    private var timer: ScheduledExecutorService? = null

    @Volatile
    private var running = false

    /** Stills saved so far. */
    val photoCount: Int get() = savedCounter.get()

    fun start() {
        photosDir.mkdirs()
        running = true
        val exec = Executors.newSingleThreadScheduledExecutor()
        timer = exec
        exec.scheduleAtFixedRate({ fire() }, intervalMs, intervalMs, TimeUnit.MILLISECONDS)
    }

    private fun fire() {
        if (!running) return
        if (inFlight.get() >= maxInFlight) return
        inFlight.incrementAndGet()
        imageCapture.takePicture(ioExecutor, object : ImageCapture.OnImageCapturedCallback() {
            override fun onCaptureSuccess(image: ImageProxy) {
                try {
                    val tUtc = clock.utcFromElapsedNanos(image.imageInfo.timestamp)
                    val w = image.width
                    val h = image.height
                    val buffer = image.planes[0].buffer
                    val bytes = ByteArray(buffer.remaining())
                    buffer.get(bytes)
                    val index = savedCounter.getAndIncrement()
                    val name = String.format("%06d.jpg", index)
                    try {
                        File(photosDir, name).outputStream().use { it.write(bytes) }
                        indexWriter.append(Records.photoIndex(tUtc, "phone/photos/$name", w, h))
                    } catch (e: Exception) {
                        Log.e(AppInfo.TAG, "Failed to write still $name: ${e.message}")
                    }
                } finally {
                    image.close()
                    inFlight.decrementAndGet()
                }
            }

            override fun onError(exception: ImageCaptureException) {
                Log.e(AppInfo.TAG, "Still capture error: ${exception.message}")
                inFlight.decrementAndGet()
            }
        })
    }

    fun stop() {
        running = false
        timer?.shutdownNow()
        timer = null
        indexWriter.close()
    }

    /** Release the index writer without ever having captured (bind failed). */
    fun cancel() {
        running = false
        indexWriter.close()
    }
}
