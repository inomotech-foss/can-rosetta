import Foundation
import NetworkExtension

/// The lifecycle of a programmatic Wi-Fi join attempt.
enum WifiJoinState: Equatable {
    case idle
    /// Programmatic joining cannot work in this environment (e.g. Simulator).
    case unavailable(String)
    case joining
    /// Verified association with the given SSID.
    case joined(String)
    /// iOS accepted the join request but the association could not be
    /// confirmed from here (see `join`) — informational, not a failure.
    case applied(String)
    case failed(String)
}

/// Joins the AutoPi's Wi-Fi access point programmatically via
/// `NEHotspotConfiguration`, so pairing never sends the user to the Settings
/// app. Owned/injected by the pairing view; `EdgeConnection` persists the
/// credentials but stays transport-agnostic (see
/// `EdgeConnection.joinAndPair(joiner:)`).
///
/// Requires the `com.apple.developer.networking.HotspotConfiguration`
/// entitlement (for `apply`) and `com.apple.developer.networking.wifi-info`
/// (for `NEHotspotNetwork.fetchCurrent`, TN3111) — declared in project.yml,
/// generated into `CanRosettaCompanion.entitlements`. Both are unmanaged: any
/// paid developer account can sign them.
@MainActor
final class WifiJoiner: ObservableObject {

    @Published private(set) var state: WifiJoinState = .idle

    /// Ask iOS to join `ssid` with the WPA2 passphrase `psk`, then try to
    /// *verify* the association actually happened. Returns `true` when the
    /// join was verified, the phone was already on that network, or iOS
    /// accepted the request but verification was inconclusive (`.applied`).
    /// `false` is reserved for apply errors, invalid credentials (both
    /// `.failed`) and the Simulator (`.unavailable`).
    @discardableResult
    func join(ssid: String, psk: String) async -> Bool {
        // NEHotspotConfiguration's initializer raises an Objective-C
        // NSInvalidArgumentException — not a Swift Error, so do/catch below
        // cannot intercept it — when the SSID is empty or over 32 UTF-8 bytes,
        // or the WPA passphrase is outside 8–63 characters. Validate before
        // constructing it so a malformed payload fails softly, not by crash.
        guard !ssid.isEmpty, ssid.utf8.count <= 32, (8...63).contains(psk.count) else {
            state = .failed("The QR carried invalid Wi-Fi credentials — re-generate the AutoPi's pairing QR.")
            return false
        }

        #if targetEnvironment(simulator)
        // NEHotspotConfigurationManager silently does nothing without real
        // Wi-Fi hardware — surface that instead of a confusing timeout.
        state = .unavailable("Wi-Fi join needs a real device")
        return false
        #else
        state = .joining

        let configuration = NEHotspotConfiguration(ssid: ssid, passphrase: psk, isWEP: false)
        // joinOnce = false is deliberate: a join-once network is torn down as
        // soon as the app backgrounds, and has been flaky since iOS 15. We
        // persist the configuration instead and remove it explicitly (see
        // `remove(ssid:)`) if that is ever needed.
        configuration.joinOnce = false

        do {
            try await apply(configuration)
        } catch let error as NSError
            where error.domain == NEHotspotConfigurationErrorDomain
            && error.code == NEHotspotConfigurationError.alreadyAssociated.rawValue {
            // Already on the AutoPi's AP (a re-pair, or the user joined by
            // hand) — that IS the outcome we wanted, so treat it as success.
            state = .joined(ssid)
            return true
        } catch {
            state = .failed(friendlyMessage(for: error))
            return false
        }

        // `apply`'s callback only means iOS *accepted* the request (the user
        // tapped "Join" in the system prompt) — not that the association
        // succeeded. Verify by polling the current network; association
        // normally completes within a few seconds.
        for _ in 0..<6 {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if await currentSSID() == ssid {
                state = .joined(ssid)
                return true
            }
        }
        // Not confirming is not failing: association can outlast the poll
        // window, and `fetchCurrent` itself can come up empty (it needs the
        // Access Wi-Fi Information entitlement and location authorization on
        // some iOS versions). `apply` was accepted, so report the join as
        // requested and let the pairing health check decide reachability.
        state = .applied(ssid)
        return true
        #endif
    }

    /// Forget a previously persisted configuration — the explicit flip side of
    /// `joinOnce = false` above. Not used in the normal flow; kept for
    /// completeness (e.g. unpairing from an AutoPi).
    func remove(ssid: String) {
        NEHotspotConfigurationManager.shared.removeConfiguration(forSSID: ssid)
        switch state {
        case .joined(ssid), .applied(ssid): state = .idle
        default: break
        }
    }

    // MARK: - Internals

    /// Async wrapper around the callback-based
    /// `NEHotspotConfigurationManager.apply`.
    private func apply(_ configuration: NEHotspotConfiguration) async throws {
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            NEHotspotConfigurationManager.shared.apply(configuration) { error in
                if let error {
                    continuation.resume(throwing: error)
                } else {
                    continuation.resume()
                }
            }
        }
    }

    /// The SSID we are currently associated with, if any. `fetchCurrent`
    /// requires the Access Wi-Fi Information entitlement (TN3111) on top of
    /// Hotspot Configuration — both are declared in project.yml. Even so it
    /// can return nil (e.g. location off), which is why an unconfirmed join
    /// is reported as `.applied`, not `.failed`.
    private func currentSSID() async -> String? {
        await withCheckedContinuation { continuation in
            NEHotspotNetwork.fetchCurrent { network in
                continuation.resume(returning: network?.ssid)
            }
        }
    }

    private func friendlyMessage(for error: Error) -> String {
        let nsError = error as NSError
        if nsError.domain == NEHotspotConfigurationErrorDomain,
           nsError.code == NEHotspotConfigurationError.userDenied.rawValue {
            return "Join was declined — tap \"Join AutoPi Wi-Fi\" to try again."
        }
        return error.localizedDescription
    }
}
