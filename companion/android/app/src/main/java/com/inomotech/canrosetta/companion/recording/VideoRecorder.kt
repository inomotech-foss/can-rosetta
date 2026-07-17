package com.inomotech.canrosetta.companion.recording

import android.annotation.SuppressLint
import android.content.Context
import android.util.Log
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.ImageProxy
import androidx.camera.video.FallbackStrategy
import androidx.camera.video.FileOutputOptions
import androidx.camera.video.Quality
import androidx.camera.video.QualitySelector
import androidx.camera.video.Recorder
import androidx.camera.video.Recording
import androidx.camera.video.VideoCapture
import androidx.camera.video.VideoRecordEvent
import androidx.core.content.ContextCompat
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.io.JsonlWriter
import com.inomotech.canrosetta.companion.io.Records
import com.inomotech.canrosetta.companion.time.Clock
import com.inomotech.canrosetta.companion.time.TimeMath
import java.io.File
import java.util.concurrent.Executor
import java.util.concurrent.atomic.AtomicInteger

/**
 * Optional dashboard capture to `phone/video.mp4` via CameraX
 * [VideoCapture]/[Recorder], plus a per-frame `phone/video_index.jsonl`.
 *
 * ## Frame index approach
 *
 * `VideoCapture`/`Recorder` writes the MP4 internally and exposes no per-encoded-
 * frame callback, so a true per-encoded-frame PTS is impractical. We instead bind
 * an [ImageAnalysis] alongside `VideoCapture` and index at the **analyzer
 * cadence**: for each analyzed frame we write `{ frame, pts, t_utc }`, where
 * `t_utc` is derived from `ImageInfo.timestamp` (treated as elapsedRealtime-domain
 * nanoseconds, the same domain as the IMU) via the shared [Clock], and `pts` is
 * seconds relative to the first analyzed frame. This is the documented fallback
 * the data-format doc allows for platforms without per-frame PTS.
 *
 * Both use cases ([videoCapture] and [imageAnalysis]) are exposed so the
 * [RecordingController] can bind them together (and drop [imageAnalysis] first if
 * the device cannot bind the full set). Video is recorded WITHOUT audio to avoid
 * needing the RECORD_AUDIO permission.
 */
class VideoRecorder(
    private val context: Context,
    private val clock: Clock,
    private val videoFile: File,
    indexFile: File,
    analysisExecutor: Executor,
) {

    private val indexWriter = JsonlWriter(indexFile)
    private val frameCounter = AtomicInteger(0)
    private var firstTimestampNanos: Long = -1L
    private var recording: Recording? = null
    private var finalizeCallback: (() -> Unit)? = null

    val videoCapture: VideoCapture<Recorder> = VideoCapture.withOutput(
        Recorder.Builder()
            .setQualitySelector(
                QualitySelector.from(Quality.HD, FallbackStrategy.higherQualityOrLowerThan(Quality.HD))
            )
            .build()
    )

    val imageAnalysis: ImageAnalysis = ImageAnalysis.Builder()
        .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
        .build()

    /** Frames indexed so far. */
    val frameCount: Int get() = frameCounter.get()

    init {
        imageAnalysis.setAnalyzer(analysisExecutor) { image -> onFrame(image) }
    }

    private fun onFrame(image: ImageProxy) {
        try {
            val ts = image.imageInfo.timestamp
            if (firstTimestampNanos < 0) firstTimestampNanos = ts
            val pts = (ts - firstTimestampNanos) / TimeMath.NANOS_PER_SECOND
            val frame = frameCounter.getAndIncrement()
            indexWriter.append(Records.videoIndex(frame, pts, clock.utcFromElapsedNanos(ts)))
        } catch (e: Exception) {
            Log.e(AppInfo.TAG, "video index frame failed: ${e.message}")
        } finally {
            image.close()
        }
    }

    /** Begin recording. Call after the use cases have been bound to a lifecycle. */
    @SuppressLint("MissingPermission") // video only; no audio requested
    fun start() {
        try {
            videoFile.delete()
        } catch (_: Exception) {
        }
        val options = FileOutputOptions.Builder(videoFile).build()
        recording = videoCapture.output
            .prepareRecording(context, options)
            .start(ContextCompat.getMainExecutor(context)) { event ->
                if (event is VideoRecordEvent.Finalize) {
                    if (event.hasError()) {
                        Log.e(AppInfo.TAG, "Video finalize error code=${event.error}")
                    }
                    indexWriter.close()
                    finalizeCallback?.invoke()
                    finalizeCallback = null
                }
            }
    }

    /**
     * Stop recording and invoke [onFinalized] once the MP4 is fully written
     * (the [VideoRecordEvent.Finalize] event). Called on the main thread.
     */
    fun stop(onFinalized: () -> Unit) {
        val rec = recording
        if (rec == null) {
            indexWriter.close()
            onFinalized()
            return
        }
        finalizeCallback = onFinalized
        rec.stop()
        recording = null
    }

    /** Release the index writer without ever having recorded (bind failed). */
    fun cancel() {
        indexWriter.close()
    }
}
