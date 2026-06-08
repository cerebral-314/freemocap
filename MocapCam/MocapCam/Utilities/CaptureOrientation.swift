import AVFoundation
import UIKit

extension AVCaptureVideoOrientation {
    var metadataValue: String {
        switch self {
        case .portrait:
            return "portrait"
        case .portraitUpsideDown:
            return "portraitUpsideDown"
        case .landscapeRight:
            return "landscapeRight"
        case .landscapeLeft:
            return "landscapeLeft"
        @unknown default:
            return "unknown"
        }
    }

    @MainActor
    static func currentInterfaceOrientation() -> AVCaptureVideoOrientation {
        if let sceneOrientation = UIApplication.shared.connectedScenes
            .compactMap({ ($0 as? UIWindowScene)?.interfaceOrientation })
            .first(where: { $0 != .unknown }),
           let videoOrientation = AVCaptureVideoOrientation(interfaceOrientation: sceneOrientation) {
            return videoOrientation
        }

        return AVCaptureVideoOrientation(deviceOrientation: UIDevice.current.orientation) ?? .landscapeRight
    }

    init?(interfaceOrientation: UIInterfaceOrientation) {
        switch interfaceOrientation {
        case .portrait:
            self = .portrait
        case .portraitUpsideDown:
            self = .portraitUpsideDown
        case .landscapeLeft:
            self = .landscapeLeft
        case .landscapeRight:
            self = .landscapeRight
        case .unknown:
            return nil
        @unknown default:
            return nil
        }
    }

    init?(deviceOrientation: UIDeviceOrientation) {
        switch deviceOrientation {
        case .portrait:
            self = .portrait
        case .portraitUpsideDown:
            self = .portraitUpsideDown
        case .landscapeLeft:
            self = .landscapeRight
        case .landscapeRight:
            self = .landscapeLeft
        default:
            return nil
        }
    }
}
