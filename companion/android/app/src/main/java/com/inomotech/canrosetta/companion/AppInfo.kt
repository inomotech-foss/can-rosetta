package com.inomotech.canrosetta.companion

/** Shared app-wide constants (logging tag, versions, clock source). */
object AppInfo {
    const val TAG = "CanRosetta"
    const val DISPLAY_NAME = "CAN-Rosetta Companion"
    const val SOFTWARE_VERSION = "can-rosetta-companion/0.1.0"
    const val SCHEMA_VERSION = "1.0.0"

    /**
     * Clock source reported in the manifest. We run full-accuracy GNSS the whole
     * session, so we report "gps" — with the honest caveat (see [time.Clock]) that
     * Android does not expose raw GNSS/PPS time, so the absolute offset is really
     * the system clock's.
     */
    const val CLOCK_SOURCE = "gps"
}
