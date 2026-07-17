package com.inomotech.canrosetta.companion.sensors

import android.content.Context
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.os.Handler
import android.os.HandlerThread
import com.inomotech.canrosetta.companion.io.Records
import com.inomotech.canrosetta.companion.time.Clock
import com.inomotech.canrosetta.companion.time.TimeMath
import org.json.JSONObject

/**
 * Fuses the individual IMU sensors into one motion record per line of
 * `phone/motion.jsonl`, at ~100 Hz, stamped with honest `t_utc`.
 *
 * Android delivers each sensor independently (unlike iOS's fused
 * `CMDeviceMotion`), so we cache the latest gyroscope / rotation-vector /
 * magnetometer / gravity values and emit one combined record on every
 * `TYPE_LINEAR_ACCELERATION` event (the primary driver — `acc` and `rot` are the
 * schema-required fields). No resampling or filtering: each record is stamped
 * with the linear-acceleration event's own timestamp.
 *
 * Units follow the schema:
 * - `acc`     = `TYPE_LINEAR_ACCELERATION` m/s^2 / 9.80665 -> g (gravity removed)
 * - `gravity` = `TYPE_GRAVITY` m/s^2 / 9.80665 -> g
 * - `rot`     = `TYPE_GYROSCOPE` rad/s (x,y,z)
 * - `att`     = `TYPE_ROTATION_VECTOR` -> getOrientation -> [roll, pitch, yaw] rad
 * - `mag`     = `TYPE_MAGNETIC_FIELD` µT (null until first sample / if absent)
 *
 * All sensor callbacks are delivered on a single dedicated [HandlerThread], so
 * the cached fields need no extra synchronisation.
 */
class MotionSource(
    context: Context,
    private val clock: Clock,
    private val hz: Int = 100,
) : SensorEventListener {

    private val sensorManager = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    private val linearAcceleration = sensorManager.getDefaultSensor(Sensor.TYPE_LINEAR_ACCELERATION)
    private val gyroscope = sensorManager.getDefaultSensor(Sensor.TYPE_GYROSCOPE)
    private val rotationVector = sensorManager.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
    private val magnetometer = sensorManager.getDefaultSensor(Sensor.TYPE_MAGNETIC_FIELD)
    private val gravity = sensorManager.getDefaultSensor(Sensor.TYPE_GRAVITY)

    /** Called for every emitted record, on the sensor handler thread. */
    var onRecord: ((JSONObject) -> Unit)? = null

    private var thread: HandlerThread? = null

    // Scratch + latest values (touched only on the sensor handler thread).
    private val rotationMatrix = FloatArray(9)
    private val orientation = FloatArray(3)
    private var latestGravity: DoubleArray? = null
    private var latestRot = DoubleArray(3)
    private var latestAtt = DoubleArray(3)
    private var latestMag: DoubleArray? = null

    val isAvailable: Boolean get() = linearAcceleration != null

    fun start() {
        val t = HandlerThread("canrosetta-motion").apply { start() }
        thread = t
        val handler = Handler(t.looper)
        val periodUs = 1_000_000 / hz
        linearAcceleration?.let { sensorManager.registerListener(this, it, periodUs, handler) }
        gyroscope?.let { sensorManager.registerListener(this, it, periodUs, handler) }
        rotationVector?.let { sensorManager.registerListener(this, it, periodUs, handler) }
        gravity?.let { sensorManager.registerListener(this, it, periodUs, handler) }
        magnetometer?.let { sensorManager.registerListener(this, it, periodUs, handler) }
    }

    fun stop() {
        sensorManager.unregisterListener(this)
        thread?.quitSafely()
        thread = null
    }

    override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}

    override fun onSensorChanged(event: SensorEvent) {
        when (event.sensor.type) {
            Sensor.TYPE_GYROSCOPE -> {
                latestRot = doubleArrayOf(
                    event.values[0].toDouble(),
                    event.values[1].toDouble(),
                    event.values[2].toDouble(),
                )
            }

            Sensor.TYPE_GRAVITY -> {
                latestGravity = doubleArrayOf(
                    TimeMath.toG(event.values[0]),
                    TimeMath.toG(event.values[1]),
                    TimeMath.toG(event.values[2]),
                )
            }

            Sensor.TYPE_MAGNETIC_FIELD -> {
                latestMag = doubleArrayOf(
                    event.values[0].toDouble(),
                    event.values[1].toDouble(),
                    event.values[2].toDouble(),
                )
            }

            Sensor.TYPE_ROTATION_VECTOR -> {
                SensorManager.getRotationMatrixFromVector(rotationMatrix, event.values)
                SensorManager.getOrientation(rotationMatrix, orientation)
                // getOrientation returns [azimuth(yaw), pitch, roll]; the schema
                // wants [roll, pitch, yaw].
                latestAtt = doubleArrayOf(
                    orientation[2].toDouble(),
                    orientation[1].toDouble(),
                    orientation[0].toDouble(),
                )
            }

            Sensor.TYPE_LINEAR_ACCELERATION -> {
                val acc = doubleArrayOf(
                    TimeMath.toG(event.values[0]),
                    TimeMath.toG(event.values[1]),
                    TimeMath.toG(event.values[2]),
                )
                val record = Records.motion(
                    tUtc = clock.utcFromElapsedNanos(event.timestamp),
                    acc = acc,
                    gravity = latestGravity,
                    rot = latestRot,
                    att = latestAtt,
                    mag = latestMag,
                )
                onRecord?.invoke(record)
            }
        }
    }
}
