package com.inomotech.canrosetta.companion.car

import android.content.Intent
import androidx.car.app.Screen
import androidx.car.app.Session
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.LifecycleOwner
import com.inomotech.canrosetta.companion.CanRosettaApplication

/**
 * One car connection. Owns the [CarHardwareLogger] for exactly as long as the
 * session lives — the car-hardware listeners are registered when the host
 * creates the session and unregistered on destroy, independent of whether a
 * recording is running (the logger itself gates writes on recording state).
 */
class CarSession(private val app: CanRosettaApplication) : Session() {

    private var logger: CarHardwareLogger? = null

    init {
        lifecycle.addObserver(object : DefaultLifecycleObserver {
            override fun onDestroy(owner: LifecycleOwner) {
                logger?.stop()
                logger = null
            }
        })
    }

    override fun onCreateScreen(intent: Intent): Screen {
        // Created here (not in init): carContext is only ready once the host
        // has attached the session.
        // Stop any prior logger first: the host can re-create the screen within
        // one session, and overwriting `logger` without stopping it would leak
        // the old listeners + coroutine scope and duplicate records.
        logger?.stop()
        val logger = CarHardwareLogger(carContext, app.recordingController).also { it.start() }
        this.logger = logger
        return StatusScreen(carContext, app, logger)
    }
}
