package com.inomotech.canrosetta.companion.io

import android.util.Log
import com.inomotech.canrosetta.companion.AppInfo
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

/**
 * A buffered, thread-safe writer that serialises records one per line
 * (newline-delimited JSON) to a file. All disk writes are funnelled through a
 * private single-thread executor, so it is safe to call [append] concurrently
 * from the sensor handler thread, the location callback and the camera analyzer.
 *
 * Used for `phone/motion.jsonl`, `phone/location.jsonl`, `phone/video_index.jsonl`
 * and `phone/photos_index.jsonl`.
 */
class JsonlWriter(file: File) {

    private val out = BufferedOutputStream(FileOutputStream(file))
    private val executor: ExecutorService = Executors.newSingleThreadExecutor()
    private val counter = AtomicLong(0)

    @Volatile
    private var closed = false

    val fileName: String = file.name

    /**
     * Append one record. The count is bumped synchronously (so [rowCount] is
     * immediately accurate for the manifest / UI); encoding and the disk write
     * happen on the writer thread. Write failures are logged and the record is
     * dropped — a bad sample must never abort a drive-long recording.
     */
    fun append(obj: JSONObject) {
        if (closed) return
        counter.incrementAndGet()
        val bytes = (obj.toString() + "\n").toByteArray(Charsets.UTF_8)
        executor.execute {
            try {
                out.write(bytes)
            } catch (e: IOException) {
                Log.e(AppInfo.TAG, "jsonl write failed ($fileName): ${e.message}")
            }
        }
    }

    /** Thread-safe row count. */
    val rowCount: Long get() = counter.get()

    /** Flush the remaining buffer and close the file. Blocks until done. */
    fun close() {
        if (closed) return
        closed = true
        executor.execute {
            try {
                out.flush()
                out.close()
            } catch (e: IOException) {
                Log.e(AppInfo.TAG, "jsonl close failed ($fileName): ${e.message}")
            }
        }
        executor.shutdown()
        try {
            executor.awaitTermination(10, TimeUnit.SECONDS)
        } catch (e: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }
}
