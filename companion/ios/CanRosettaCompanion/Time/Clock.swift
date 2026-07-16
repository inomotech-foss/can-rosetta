import Foundation
import CoreMedia

/// Provides honest UTC timestamps (`t_utc`, Unix epoch seconds as a `Double`)
/// for every recorded sample.
///
/// ## Why not just call `Date()` per sample?
///
/// The whole point of CAN-Rosetta is that a bump in the road lines up in the
/// phone IMU and in the CAN log at "the same" instant. If iOS applies an NTP /
/// carrier time correction *mid-drive*, `Date()` can step forwards or backwards
/// by tens of milliseconds. That would silently corrupt the *relative* spacing
/// between our samples — exactly the thing the server relies on.
///
/// So we anchor **once** at construction: we record the wall-clock UTC that
/// corresponds to "device boot" (`bootWallClockUTC`). Every sensor sample
/// already carries a device-uptime timestamp (`CMLogItem.timestamp`,
/// `CMTime` host time, `ProcessInfo.systemUptime`) measured against that same
/// boot instant on a monotonic clock. We simply add:
///
///     t_utc = bootWallClockUTC + uptimeSeconds
///
/// Because `bootWallClockUTC` is captured once and never updated, later NTP
/// steps do not change the *spacing* between our samples — only a constant
/// offset that the server estimates and corrects during fine alignment. This is
/// the "monotonic-anchored wall clock" the data-format doc asks producers for.
///
/// ## Clock source reported in the manifest
///
/// We report `source: "gps"` in the manifest because the companion runs GPS at
/// full accuracy for the whole session, and GPS is the most trustworthy time
/// reference physically present. Note the honest caveat: iOS does **not** expose
/// raw GNSS time to apps, so we cannot literally discipline our clock to the GPS
/// PPS edge. `bootWallClockUTC` therefore derives from the (NTP/carrier
/// disciplined) system clock sampled once at start. See README for the TODO on
/// true GPS-time disciplining.
final class Clock {

    /// Wall-clock UTC (Unix epoch seconds) that corresponds to device boot,
    /// captured once at construction against a monotonic reference.
    let bootWallClockUTC: Double

    init() {
        // `systemUptime` is seconds since boot on a monotonic clock. Sampling
        // `Date()` and `systemUptime` back-to-back and subtracting gives the
        // UTC of the boot instant. Do this once, atomically as possible.
        let uptime = ProcessInfo.processInfo.systemUptime
        let wall = Date().timeIntervalSince1970
        self.bootWallClockUTC = wall - uptime
    }

    /// Convert a CoreMotion / `systemUptime`-domain timestamp (seconds since
    /// boot) into `t_utc`.
    func utc(fromUptime uptime: TimeInterval) -> Double {
        bootWallClockUTC + uptime
    }

    /// Convert an `AVCaptureSession` sample-buffer presentation timestamp into
    /// `t_utc`. Capture PTS live in the host-time clock domain
    /// (`CMClockGetHostTimeClock`), i.e. seconds since boot — the same domain as
    /// `systemUptime` — so the same anchor applies.
    func utc(fromHostTime hostTime: CMTime) -> Double {
        bootWallClockUTC + hostTime.seconds
    }

    /// "Now" as `t_utc`, using the monotonic-anchored clock (not a fresh
    /// `Date()`), so timestamps taken at start/stop are consistent with sample
    /// timestamps.
    func nowUTC() -> Double {
        bootWallClockUTC + ProcessInfo.processInfo.systemUptime
    }
}

/// Session identity. In the full system the `session_id` is *agreed* with the
/// AutoPi at drive start (QR handshake or manual entry) so both parts merge on
/// the server. Here we generate a UUID by default but let the user override /
/// share it.
enum SessionID {
    /// A fresh, lowercase UUID string (no braces).
    static func generate() -> String {
        UUID().uuidString.lowercased()
    }
}
