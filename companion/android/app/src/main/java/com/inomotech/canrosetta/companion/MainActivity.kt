package com.inomotech.canrosetta.companion

import android.Manifest
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.core.content.ContextCompat
import com.inomotech.canrosetta.companion.recording.RecordingController
import com.inomotech.canrosetta.companion.remote.EdgeConnection
import com.inomotech.canrosetta.companion.ui.MainScreen
import com.inomotech.canrosetta.companion.ui.RemoteControlScreen
import com.inomotech.canrosetta.companion.ui.theme.CanRosettaTheme

class MainActivity : ComponentActivity() {

    private lateinit var controller: RecordingController
    private lateinit var connection: EdgeConnection

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        controller.setLocationPermissionGranted(hasPermission(Manifest.permission.ACCESS_FINE_LOCATION))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        controller = RecordingController(applicationContext, this)
        connection = EdgeConnection(applicationContext)
        controller.setLocationPermissionGranted(hasPermission(Manifest.permission.ACCESS_FINE_LOCATION))

        requestNeededPermissions()

        setContent {
            CanRosettaTheme {
                var showRemote by rememberSaveable { mutableStateOf(false) }
                if (showRemote) {
                    RemoteControlScreen(controller, connection, onBack = { showRemote = false })
                } else {
                    MainScreen(controller, connection, onOpenRemote = { showRemote = true })
                }
            }
        }
    }

    private fun requestNeededPermissions() {
        val needed = buildList {
            if (!hasPermission(Manifest.permission.ACCESS_FINE_LOCATION)) {
                add(Manifest.permission.ACCESS_FINE_LOCATION)
            }
            if (!hasPermission(Manifest.permission.CAMERA)) {
                add(Manifest.permission.CAMERA)
            }
        }
        if (needed.isNotEmpty()) {
            permissionLauncher.launch(needed.toTypedArray())
        }
    }

    private fun hasPermission(permission: String): Boolean =
        ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED
}
