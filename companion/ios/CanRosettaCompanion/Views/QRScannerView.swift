import SwiftUI
import AVFoundation

/// A live QR viewfinder backed by `AVCaptureMetadataOutput` (`.qr`). Emits the
/// first decoded string payload via `onCode`. If the camera is unavailable
/// (e.g. the Simulator, or permission denied), it renders a dark placeholder and
/// reports through `onUnavailable` so the caller can show the manual fallback.
struct QRScannerView: UIViewRepresentable {
    var isActive: Bool = true
    let onCode: (String) -> Void
    var onUnavailable: ((String) -> Void)?

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        context.coordinator.attach(to: view, onUnavailable: onUnavailable)
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {
        context.coordinator.setRunning(isActive)
    }

    static func dismantleUIView(_ uiView: PreviewView, coordinator: Coordinator) {
        coordinator.tearDown()
    }

    // MARK: - Preview UIView

    final class PreviewView: UIView {
        override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
        var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
    }

    // MARK: - Coordinator

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        private let onCode: (String) -> Void
        private let session = AVCaptureSession()
        private let queue = DispatchQueue(label: "qr.session")
        private var configured = false
        private var didEmit = false

        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func attach(to view: PreviewView, onUnavailable: ((String) -> Void)?) {
            view.previewLayer.session = session
            view.previewLayer.videoGravity = .resizeAspectFill

            guard let device = AVCaptureDevice.default(for: .video),
                  let input = try? AVCaptureDeviceInput(device: device),
                  session.canAddInput(input) else {
                onUnavailable?("Camera unavailable")
                return
            }
            session.beginConfiguration()
            session.addInput(input)
            let output = AVCaptureMetadataOutput()
            if session.canAddOutput(output) {
                session.addOutput(output)
                output.setMetadataObjectsDelegate(self, queue: DispatchQueue.main)
                if output.availableMetadataObjectTypes.contains(.qr) {
                    output.metadataObjectTypes = [.qr]
                }
            }
            session.commitConfiguration()
            configured = true
        }

        func setRunning(_ running: Bool) {
            guard configured else { return }
            queue.async { [session] in
                if running, !session.isRunning { session.startRunning() }
                else if !running, session.isRunning { session.stopRunning() }
            }
        }

        func tearDown() {
            queue.async { [session] in
                if session.isRunning { session.stopRunning() }
            }
        }

        func metadataOutput(_ output: AVCaptureMetadataOutput,
                            didOutput metadataObjects: [AVMetadataObject],
                            from connection: AVCaptureConnection) {
            guard !didEmit,
                  let obj = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
                  obj.type == .qr, let payload = obj.stringValue else { return }
            didEmit = true
            onCode(payload)
        }
    }
}

/// The pairing payload carried by the AutoPi's QR code.
struct PairingPayload: Decodable {
    let host: String
    let token: String
    let sessionId: String?

    enum CodingKeys: String, CodingKey {
        case host, token
        case sessionId = "session_id"
    }

    static func decode(_ text: String) -> PairingPayload? {
        guard let data = text.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(PairingPayload.self, from: data)
    }
}
