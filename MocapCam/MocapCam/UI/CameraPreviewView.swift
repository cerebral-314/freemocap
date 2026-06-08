import AVFoundation
import SwiftUI
import UIKit

struct CameraPreviewView: UIViewRepresentable {
    let session: AVCaptureSession
    let videoOrientation: AVCaptureVideoOrientation

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.videoPreviewLayer.session = session
        view.videoPreviewLayer.videoGravity = .resizeAspectFill
        view.updateVideoOrientation(videoOrientation)
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {
        uiView.videoPreviewLayer.session = session
        uiView.updateVideoOrientation(videoOrientation)
    }
}

final class PreviewView: UIView {
    override class var layerClass: AnyClass {
        AVCaptureVideoPreviewLayer.self
    }

    var videoPreviewLayer: AVCaptureVideoPreviewLayer {
        layer as! AVCaptureVideoPreviewLayer
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        updateVideoOrientation()
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        updateVideoOrientation()
    }

    func updateVideoOrientation(_ orientation: AVCaptureVideoOrientation? = nil) {
        guard let connection = videoPreviewLayer.connection,
              connection.isVideoOrientationSupported else {
            return
        }

        if let orientation {
            connection.videoOrientation = orientation
        } else if let interfaceOrientation = window?.windowScene?.interfaceOrientation,
           let videoOrientation = AVCaptureVideoOrientation(interfaceOrientation: interfaceOrientation) {
            connection.videoOrientation = videoOrientation
        } else {
            connection.videoOrientation = AVCaptureVideoOrientation.currentInterfaceOrientation()
        }
    }
}
