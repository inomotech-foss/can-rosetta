package com.inomotech.canrosetta.companion.car

import android.Manifest
import android.content.pm.PackageManager
import android.util.Log
import androidx.car.app.CarContext
import androidx.car.app.hardware.CarHardwareManager
import androidx.car.app.hardware.common.CarValue
import androidx.car.app.hardware.common.OnCarDataAvailableListener
import androidx.car.app.hardware.info.Accelerometer
import androidx.car.app.hardware.info.CarHardwareLocation
import androidx.car.app.hardware.info.CarInfo
import androidx.car.app.hardware.info.CarSensors
import androidx.car.app.hardware.info.Compass
import androidx.car.app.hardware.info.EnergyLevel
import androidx.car.app.hardware.info.Gyroscope
import androidx.car.app.hardware.info.Mileage
import androidx.car.app.hardware.info.Model
import androidx.car.app.hardware.info.Speed
import androidx.core.content.ContextCompat
import com.inomotech.canrosetta.companion.AppInfo
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.time.Clock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.drop
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * Subscribes to the host's car-hardware feeds ([CarInfo] + [CarSensors]) for
 * the lifetime of one car session and maps every callback/fetch — success or
 * not — to a `phone/car_hw.jsonl` record via [CarHwRecords].
 *
 * Records are handed to [RecordingController.appendCarHardware], which accepts
 * them ONLY while a recording session is active (the writer lives with the
 * session). Because availability facts can be learned while idle (the car
 * connects, the model fetch answers, a permission is missing) and those facts
 * are the deliverable, one-shot facts are cached in [pendingFacts] and
 * re-stamped + flushed into the stream whenever a recording starts, so every
 * session file answers "does this head unit forward anything?" even when the
 * car connected long before the drive.
 *
 * Timestamps use a monotonic-anchored [Clock] — the same clock construction
 * `motion.jsonl` records use — so `t_utc` lives in the phone-clock domain the
 * shared contract requires.
 *
 * All callbacks are delivered on the main executor, the same thread the
 * controller mutates its session state on, so no extra synchronisation is
 * needed anywhere in this class.
 */
class CarHardwareLogger(
    private val carContext: CarContext,
    private val controller: RecordingController,
) {
    private val clock = Clock()
    private val mainExecutor = ContextCompat.getMainExecutor(carContext)
    private val scope = CoroutineScope(Dispatchers.Main.immediate + SupervisorJob())

    /** Latest per-kind contract status, formatted for the car screen's "car data" row. */
    private val _summary = MutableStateFlow(SUMMARY_IDLE)
    val summary: StateFlow<String> = _summary

    private val kindStatus = LinkedHashMap<String, String>()

    /** One-shot availability facts learned while not recording (see class doc). */
    private val pendingFacts = LinkedHashMap<String, JSONObject>()

    private var carInfo: CarInfo? = null
    private var carSensors: CarSensors? = null
    private val registeredKinds = mutableSetOf<String>()
    private var started = false

    // MARK: - Lifecycle

    /** Register all feeds; idempotent. Called when the car session is created. */
    fun start() {
        if (started) return
        started = true
        registerAll()
        // Flush the cached one-shot facts into every session that starts while
        // the car is connected (rising edge of isRecording). drop(1) skips the
        // collector's INITIAL replay of the current state: if a session is
        // already recording, registerAll() above wrote the stubs inline, so
        // replaying that same state here would duplicate every fact — only
        // genuine later false->true transitions should flush.
        scope.launch {
            controller.status
                .map { it.isRecording }
                .distinctUntilChanged()
                .drop(1)
                .collect { recording -> if (recording) flushPendingFacts() }
        }
    }

    /** Unregister everything. Called when the car session is destroyed. */
    fun stop() {
        unregisterAll()
        scope.cancel()
        started = false
    }

    /**
     * Drop and re-run all registrations — called after the parked-only
     * permission dialog resolves, so newly granted feeds go live immediately.
     */
    fun refreshRegistrations() {
        unregisterAll()
        registerAll()
    }

    // MARK: - Registration

    private fun registerAll() {
        // Stale stubs (e.g. an old permission_denied) must not outlive a
        // re-registration; each guard below re-adds what still applies.
        pendingFacts.clear()

        val hardware = try {
            carContext.getCarService(CarHardwareManager::class.java)
        } catch (e: Exception) {
            // Host without car-hardware support (car API level < 3 or a
            // non-compliant head unit) — that answer is worth a record per kind.
            Log.w(AppInfo.TAG, "CarHardwareManager unavailable: ${e.message}")
            val t = clock.nowUtc()
            for (kind in ALL_KINDS) {
                deliver(kind, CarHwRecords.STATUS_ERROR,
                    CarHwRecords.error(t, kind, "car_hardware_unsupported"), oneShot = true)
            }
            return
        }
        val info = hardware.carInfo
        val sensors = hardware.carSensors
        carInfo = info
        carSensors = sensors

        // Model is a one-shot fetch (once per car session), not a listener.
        guard("model") { info.fetchModel(mainExecutor, modelListener) }
        guard("energy", PERM_CAR_FUEL) {
            info.addEnergyLevelListener(mainExecutor, energyListener)
        }
        guard("speed", PERM_CAR_SPEED) {
            info.addSpeedListener(mainExecutor, speedListener)
        }
        guard("mileage", PERM_CAR_MILEAGE) {
            info.addMileageListener(mainExecutor, mileageListener)
        }
        // NORMAL rate, not FASTEST: this stream is for availability + a coarse
        // reference, and the callbacks run on the main thread — FASTEST would
        // serialise hundreds of records/s there for no benefit.
        // The car GNSS feed is gated by the phone's own location permission.
        guard("location", Manifest.permission.ACCESS_FINE_LOCATION) {
            sensors.addCarHardwareLocationListener(
                CarSensors.UPDATE_RATE_NORMAL, mainExecutor, locationListener)
        }
        guard("accelerometer") {
            sensors.addAccelerometerListener(
                CarSensors.UPDATE_RATE_NORMAL, mainExecutor, accelerometerListener)
        }
        guard("gyroscope") {
            sensors.addGyroscopeListener(
                CarSensors.UPDATE_RATE_NORMAL, mainExecutor, gyroscopeListener)
        }
        guard("compass") {
            sensors.addCompassListener(
                CarSensors.UPDATE_RATE_NORMAL, mainExecutor, compassListener)
        }
    }

    /**
     * Register one feed: a missing runtime permission becomes an
     * "unavailable/permission_denied" record (still a record — the availability
     * data is the deliverable), a throwing host becomes an "error" record.
     */
    private inline fun guard(kind: String, permission: String? = null, register: () -> Unit) {
        if (permission != null &&
            ContextCompat.checkSelfPermission(carContext, permission) != PackageManager.PERMISSION_GRANTED
        ) {
            deliver(kind, CarHwRecords.STATUS_UNAVAILABLE,
                CarHwRecords.permissionDenied(clock.nowUtc(), kind), oneShot = true)
            return
        }
        try {
            register()
            registeredKinds.add(kind)
        } catch (e: Exception) {
            Log.w(AppInfo.TAG, "car_hw $kind registration failed: ${e.message}")
            deliver(kind, CarHwRecords.STATUS_ERROR,
                CarHwRecords.error(clock.nowUtc(), kind, e.message ?: e.javaClass.simpleName),
                oneShot = true)
        }
    }

    private fun unregisterAll() {
        val info = carInfo
        val sensors = carSensors
        // Each removal is isolated: a throw on one listener (host gone away)
        // must not leave the rest registered, or a later re-register would
        // double-subscribe them and deliver every callback twice.
        fun tryRemove(block: () -> Unit) = try {
            block()
        } catch (e: Exception) {
            Log.w(AppInfo.TAG, "car_hw unregister failed: ${e.message}")
        }
        if (info != null) {
            if ("energy" in registeredKinds) tryRemove { info.removeEnergyLevelListener(energyListener) }
            if ("speed" in registeredKinds) tryRemove { info.removeSpeedListener(speedListener) }
            if ("mileage" in registeredKinds) tryRemove { info.removeMileageListener(mileageListener) }
        }
        if (sensors != null) {
            if ("location" in registeredKinds) {
                tryRemove { sensors.removeCarHardwareLocationListener(locationListener) }
            }
            if ("accelerometer" in registeredKinds) {
                tryRemove { sensors.removeAccelerometerListener(accelerometerListener) }
            }
            if ("gyroscope" in registeredKinds) tryRemove { sensors.removeGyroscopeListener(gyroscopeListener) }
            if ("compass" in registeredKinds) tryRemove { sensors.removeCompassListener(compassListener) }
        }
        registeredKinds.clear()
    }

    // MARK: - Listeners (main executor)

    private val modelListener = OnCarDataAvailableListener<Model> { model ->
        val status = CarHwRecords.aggregateStatus(
            listOf(model.manufacturer.status, model.name.status, model.year.status))
        // One-shot: fetched once per car session, replayed into each recording.
        deliver("model", status, CarHwRecords.model(
            clock.nowUtc(), status,
            model.manufacturer.successValue(),
            model.name.successValue(),
            model.year.successValue(),
        ), oneShot = true)
    }

    private val energyListener = OnCarDataAvailableListener<EnergyLevel> { energy ->
        val status = CarHwRecords.aggregateStatus(listOf(
            energy.batteryPercent.status, energy.fuelPercent.status,
            energy.rangeRemainingMeters.status, energy.energyIsLow.status,
        ))
        deliver("energy", status, CarHwRecords.energy(
            clock.nowUtc(), status,
            energy.batteryPercent.successValue()?.toDouble(),
            energy.fuelPercent.successValue()?.toDouble(),
            energy.rangeRemainingMeters.successValue()?.toDouble(),
            energy.energyIsLow.successValue(),
        ))
    }

    private val speedListener = OnCarDataAvailableListener<Speed> { speed ->
        val status = CarHwRecords.aggregateStatus(listOf(
            speed.rawSpeedMetersPerSecond.status, speed.displaySpeedMetersPerSecond.status,
        ))
        deliver("speed", status, CarHwRecords.speed(
            clock.nowUtc(), status,
            speed.rawSpeedMetersPerSecond.successValue()?.toDouble(),
            speed.displaySpeedMetersPerSecond.successValue()?.toDouble(),
        ))
    }

    private val mileageListener = OnCarDataAvailableListener<Mileage> { mileage ->
        val status = CarHwRecords.statusString(mileage.odometerMeters.status)
        deliver("mileage", status, CarHwRecords.mileage(
            clock.nowUtc(), status,
            mileage.odometerMeters.successValue()?.toDouble(),
        ))
    }

    private val locationListener = OnCarDataAvailableListener<CarHardwareLocation> { hwLocation ->
        val cv = hwLocation.location
        val status = CarHwRecords.statusString(cv.status)
        val loc = cv.successValue()
        deliver("location", status, CarHwRecords.location(
            clock.nowUtc(), status,
            loc?.latitude,
            loc?.longitude,
            loc?.takeIf { it.hasAccuracy() }?.accuracy?.toDouble(),
            loc?.takeIf { it.hasSpeed() }?.speed?.toDouble(),
            loc?.takeIf { it.hasBearing() }?.bearing?.toDouble(),
        ))
    }

    private val accelerometerListener =
        OnCarDataAvailableListener<Accelerometer> { deliverAxes("accelerometer", it.forces) }

    private val gyroscopeListener =
        OnCarDataAvailableListener<Gyroscope> { deliverAxes("gyroscope", it.rotations) }

    private val compassListener =
        OnCarDataAvailableListener<Compass> { deliverAxes("compass", it.orientations) }

    private fun deliverAxes(kind: String, values: CarValue<List<Float>>) {
        val status = CarHwRecords.statusString(values.status)
        val v = values.successValue()
        deliver(kind, status, CarHwRecords.axes(
            clock.nowUtc(), kind, status,
            v?.getOrNull(0)?.toDouble(),
            v?.getOrNull(1)?.toDouble(),
            v?.getOrNull(2)?.toDouble(),
        ))
    }

    // MARK: - Delivery

    /**
     * Route one record: update the live summary, write it if a session is
     * recording, and manage the one-shot cache. [oneShot] facts (model fetch,
     * registration stubs) stay cached even after being written: fetchModel
     * answers once per CAR session, but several RECORDING sessions can start
     * within one car session and each needs the fact replayed. Continuous
     * feeds instead clear any stale stub for their kind — a live callback
     * proves the feed works, so e.g. a pre-grant permission_denied stub must
     * not replay into a later session.
     */
    private fun deliver(kind: String, status: String, record: JSONObject, oneShot: Boolean = false) {
        // Rebuild the summary only when this kind's status actually changed; the
        // car screen reads it at ~1 Hz, so recomputing on every callback (up to
        // NORMAL-rate for the sensor feeds) is wasted work.
        if (kindStatus.put(kind, status) != status) _summary.value = formatSummary()
        if (oneShot) pendingFacts[kind] = record else pendingFacts.remove(kind)
        if (controller.status.value.isRecording) {
            controller.appendCarHardware(record)
        }
    }

    private fun flushPendingFacts() {
        val t = clock.nowUtc()
        for (record in pendingFacts.values) {
            // Re-stamp: the fact is re-asserted at session start, not at the
            // long-gone moment it was first learned.
            record.put("t_utc", t)
            controller.appendCarHardware(record)
        }
    }

    private fun formatSummary(): String {
        val parts = SUMMARY_ORDER.mapNotNull { (kind, label) ->
            kindStatus[kind]?.let { "$label ${abbrev(it)}" }
        }
        return if (parts.isEmpty()) SUMMARY_IDLE else parts.joinToString(" · ")
    }

    private fun abbrev(status: String): String = when (status) {
        CarHwRecords.STATUS_SUCCESS -> "ok"
        CarHwRecords.STATUS_UNAVAILABLE -> "n/a"
        CarHwRecords.STATUS_UNIMPLEMENTED -> "n/i"
        else -> "err"
    }

    private fun <T> CarValue<T>.successValue(): T? =
        if (status == CarValue.STATUS_SUCCESS) value else null

    companion object {
        // GMS car-data runtime permissions gating the projected car-hardware APIs.
        const val PERM_CAR_SPEED = "com.google.android.gms.permission.CAR_SPEED"
        const val PERM_CAR_MILEAGE = "com.google.android.gms.permission.CAR_MILEAGE"
        const val PERM_CAR_FUEL = "com.google.android.gms.permission.CAR_FUEL"

        /**
         * What the car screen's parked-only "grant car data access" action
         * requests. ACCESS_FINE_LOCATION is deliberately absent: the phone UI
         * already requests it on launch, and mixing phone-permission prompts
         * into the car-data dialog muddies both.
         */
        val CAR_DATA_PERMISSIONS: List<String> =
            listOf(PERM_CAR_SPEED, PERM_CAR_MILEAGE, PERM_CAR_FUEL)

        private const val SUMMARY_IDLE = "no car data yet"

        /** Display order + short labels for the car screen's one-line summary. */
        private val SUMMARY_ORDER = listOf(
            "speed" to "speed",
            "mileage" to "odo",
            "energy" to "soc",
            "location" to "loc",
            "model" to "model",
            "accelerometer" to "imu",
        )

        private val ALL_KINDS = listOf(
            "model", "energy", "speed", "mileage",
            "location", "accelerometer", "gyroscope", "compass",
        )
    }
}
