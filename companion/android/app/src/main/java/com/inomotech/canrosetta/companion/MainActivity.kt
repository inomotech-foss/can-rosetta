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
import com.inomotech.canrosetta.companion.ui.RemoteControlScreen
import com.inomotech.canrosetta.companion.ui.flow.DriveFlowScreen
import com.inomotech.canrosetta.companion.ui.flow.DriveFlowViewModel
import com.inomotech.canrosetta.companion.ui.theme.CanRosettaTheme

class MainActivity : ComponentActivity() {

    private lateinit var controller: RecordingController
    private lateinit var connection: EdgeConnection
    private lateinit var flow: DriveFlowViewModel

    private val permissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestMultiplePermissions()
    ) {
        controller.setLocationPermissionGranted(hasPermission(Manifest.permission.ACCESS_FINE_LOCATION))
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // The controller/connection are process singletons owned by the
        // application (the Android Auto car session must reach them without
        // this activity existing); the drive-flow navigation state stays
        // activity-scoped as before.
        val app = application as CanRosettaApplication
        controller = app.recordingController
        connection = app.edgeConnection
        flow = DriveFlowViewModel(controller, connection)
        controller.setLocationPermissionGranted(hasPermission(Manifest.permission.ACCESS_FINE_LOCATION))

        requestNeededPermissions()

        setContent {
            CanRosettaTheme {
                var showRemote by rememberSaveable { mutableStateOf(false) }
                if (showRemote) {
                    RemoteControlScreen(controller, connection, onBack = { showRemote = false })
                } else {
                    DriveFlowScreen(controller, connection, flow, onOpenAdvanced = { showRemote = true })
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
