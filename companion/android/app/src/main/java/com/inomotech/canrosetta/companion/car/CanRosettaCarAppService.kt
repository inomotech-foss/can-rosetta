package com.inomotech.canrosetta.companion.car

import android.content.pm.ApplicationInfo
import androidx.car.app.CarAppService
import androidx.car.app.Session
import androidx.car.app.validation.HostValidator
import com.inomotech.canrosetta.companion.CanRosettaApplication

/**
 * Android Auto entry point (declared in the manifest under the IOT category —
 * see the manifest comment). The host binds here and gets one [CarSession]
 * per car connection.
 */
class CanRosettaCarAppService : CarAppService() {

    /**
     * Debug builds accept any host so the Desktop Head Unit / emulator hosts
     * (whose signatures are not in the production allowlist) can bind during
     * development. Release builds use the library's vetted allowlist of
     * Google-signed hosts — the host-validation path CVE-2024-10382 is about,
     * which is why the dependency floor is car.app 1.7.0.
     */
    override fun createHostValidator(): HostValidator =
        if (applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE != 0) {
            HostValidator.ALLOW_ALL_HOSTS_VALIDATOR
        } else {
            HostValidator.Builder(applicationContext)
                .addAllowedHosts(androidx.car.app.R.array.hosts_allowlist_sample)
                .build()
        }

    override fun onCreateSession(): Session =
        CarSession(application as CanRosettaApplication)
}
