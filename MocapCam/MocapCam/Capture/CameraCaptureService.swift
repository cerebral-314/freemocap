import AVFoundation
import CoreMedia
import Foundation
import simd
import UIKit

final class CameraCaptureService: NSObject, @unchecked Sendable {
    enum CameraError: LocalizedError {
        case cameraAccessDenied
        case noCameraAvailable
        case cannotAddInput
        case cannotAddOutput

        var errorDescription: String? {
            switch self {
            case .cameraAccessDenied:
                return "Camera access is denied."
            case .noCameraAvailable:
                return "No rear camera is available."
            case .cannotAddInput:
                return "The rear camera input could not be added."
            case .cannotAddOutput:
                return "The video output could not be added."
            }
        }
    }

    let session = AVCaptureSession()
    var frameHandler: ((CMSampleBuffer, CameraFrameMetadata) -> Void)?
    var depthFrameHandler: ((DepthFrame) -> Void)?

    private let sessionQueue = DispatchQueue(label: "org.freemocap.mocapcam.camera.session")
    private let videoOutputQueue = DispatchQueue(label: "org.freemocap.mocapcam.camera.frames")
    private let depthOutputQueue = DispatchQueue(label: "org.freemocap.mocapcam.camera.depth")
    private let depthProcessingQueue = DispatchQueue(label: "org.freemocap.mocapcam.camera.depth.processing", qos: .userInitiated)
    private let videoOutput = AVCaptureVideoDataOutput()
    private let depthOutput = AVCaptureDepthDataOutput()
    private var hasConfiguredSession = false
    private var frameIndex: Int64 = 0
    private var depthFrameIndex: Int64 = 0
    private var latestRGBFrameIndex: Int64?
    private var sessionID = DeviceIdentity.defaultSessionID()
    private var deviceID = DeviceIdentity.defaultDeviceID()
    private var activeCamera: AVCaptureDevice?
    private var settings = CameraSettings.defaultSettings
    private let orientationLock = NSLock()
    private var videoOrientation: AVCaptureVideoOrientation = .landscapeRight
    private(set) var depthSupported = false

    func currentCameraSettings() -> CameraSettings {
        sessionQueue.sync {
            settings
        }
    }

    func supportedCameraSettings() -> SupportedCameraSettings {
        sessionQueue.sync {
            let camera = activeCamera ?? camera(for: settings.cameraSelection) ?? camera(for: .autoBack)
            let resolutions = CameraResolution.allCases.filter { resolution in
                session.canSetSessionPreset(sessionPreset(for: resolution))
            }
            let fpsValues = [24, 30, 60, 120].filter { fps in
                guard let camera else {
                    return fps <= 60
                }
                return supportsFrameRate(fps, camera: camera)
            }

            return SupportedCameraSettings(
                cameraSelections: availableCameraOptions(),
                resolutions: resolutions.isEmpty ? CameraResolution.allCases : resolutions,
                fps: fpsValues.isEmpty ? SupportedCameraSettings.defaultSettings.fps : fpsValues,
                exposureModes: CameraControlMode.allCases,
                focusModes: CameraControlMode.allCases,
                whiteBalanceModes: CameraControlMode.allCases,
                depthModes: supportedDepthModes(for: camera)
            )
        }
    }

    func updateIdentity(sessionID: String, deviceID: String) {
        sessionQueue.async {
            self.sessionID = sessionID
            self.deviceID = deviceID
        }
    }

    func updateVideoOrientation(_ orientation: AVCaptureVideoOrientation) {
        setVideoOrientation(orientation)
        sessionQueue.async {
            self.applyVideoOrientationToOutputs()
        }
    }

    func requestAccessAndStart(completion: @escaping (Result<Void, Error>) -> Void) {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            configureAndStart(completion: completion)
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                guard granted else {
                    completion(.failure(CameraError.cameraAccessDenied))
                    return
                }
                self?.configureAndStart(completion: completion)
            }
        default:
            completion(.failure(CameraError.cameraAccessDenied))
        }
    }

    func stopRunning() {
        sessionQueue.async {
            guard self.session.isRunning else {
                return
            }
            self.session.stopRunning()
        }
    }

    private func configureAndStart(completion: @escaping (Result<Void, Error>) -> Void) {
        sessionQueue.async {
            do {
                if !self.hasConfiguredSession {
                    try self.configureSession()
                    self.hasConfiguredSession = true
                }

                if !self.session.isRunning {
                    self.session.startRunning()
                }
                completion(.success(()))
            } catch {
                completion(.failure(error))
            }
        }
    }

    private func configureSession() throws {
        session.beginConfiguration()
        defer {
            session.commitConfiguration()
        }

        applySessionPreset(settings.resolution)
        try configureVideoOutputIfNeeded()
        try configureSelectedCamera()
        applyVideoOrientationToOutputs()
    }

    func applyCameraSettings(_ newSettings: CameraSettings, completion: (@Sendable () -> Void)? = nil) {
        sessionQueue.async {
            self.settings = self.sanitizedSettings(newSettings)

            self.session.beginConfiguration()
            defer {
                self.session.commitConfiguration()
                completion?()
            }

            do {
                self.applySessionPreset(self.settings.resolution)
                try self.configureVideoOutputIfNeeded()
                try self.configureSelectedCamera()
                self.applyVideoOrientationToOutputs()
            } catch {
                return
            }
        }
    }

    func applyCameraSettings(lockExposure: Bool?, lockFocus: Bool?, lockWhiteBalance: Bool?) {
        var updatedSettings = currentCameraSettings()
        if let lockExposure {
            updatedSettings.exposureMode = lockExposure ? .locked : .continuous
        }
        if let lockFocus {
            updatedSettings.focusMode = lockFocus ? .locked : .continuous
        }
        if let lockWhiteBalance {
            updatedSettings.whiteBalanceMode = lockWhiteBalance ? .locked : .continuous
        }
        applyCameraSettings(updatedSettings)
    }

    private func configureVideoOutputIfNeeded() throws {
        videoOutput.alwaysDiscardsLateVideoFrames = false
        videoOutput.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_420YpCbCr8BiPlanarFullRange
        ]
        videoOutput.setSampleBufferDelegate(self, queue: videoOutputQueue)

        guard !session.outputs.contains(where: { $0 === videoOutput }) else {
            return
        }

        guard session.canAddOutput(videoOutput) else {
            throw CameraError.cannotAddOutput
        }
        session.addOutput(videoOutput)
    }

    private func configureSelectedCamera() throws {
        guard let camera = camera(for: settings.cameraSelection) ?? camera(for: .autoBack) else {
            throw CameraError.noCameraAvailable
        }

        if activeCamera?.uniqueID != camera.uniqueID {
            for input in session.inputs {
                session.removeInput(input)
            }

            let input = try AVCaptureDeviceInput(device: camera)
            guard session.canAddInput(input) else {
                throw CameraError.cannotAddInput
            }
            session.addInput(input)
            activeCamera = camera
        }

        configureDepthOutputIfAvailable(for: camera)
        applyDeviceSettings(settings, to: camera)

        if let connection = videoOutput.connection(with: .video),
           connection.isCameraIntrinsicMatrixDeliverySupported {
            connection.isCameraIntrinsicMatrixDeliveryEnabled = true
        }
    }

    private func availableCameraOptions() -> [CameraSelectionOption] {
        var options = [
            CameraSelectionOption(
                id: .autoBack,
                displayName: CameraSelection.autoBack.displayName,
                depthSupported: cameraSelectionSupportsLidarDepth(.autoBack)
            )
        ]

        for selection in CameraSelection.allCases where selection != .autoBack {
            guard camera(for: selection) != nil else {
                continue
            }
            options.append(
                CameraSelectionOption(
                    id: selection,
                    displayName: selection.displayName,
                    depthSupported: cameraSelectionSupportsLidarDepth(selection)
                )
            )
        }

        return options
    }

    private func camera(for selection: CameraSelection) -> AVCaptureDevice? {
        switch selection {
        case .autoBack:
            return firstCamera(
                deviceTypes: [
                    .builtInLiDARDepthCamera,
                    .builtInTripleCamera,
                    .builtInDualWideCamera,
                    .builtInDualCamera,
                    .builtInWideAngleCamera
                ]
            ) ?? AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back)
        case .lidar:
            return firstCamera(deviceTypes: [.builtInLiDARDepthCamera])
        case .triple:
            return firstCamera(deviceTypes: [.builtInTripleCamera])
        case .dualWide:
            return firstCamera(deviceTypes: [.builtInDualWideCamera])
        case .dual:
            return firstCamera(deviceTypes: [.builtInDualCamera])
        case .wide:
            return firstCamera(deviceTypes: [.builtInWideAngleCamera])
        case .ultraWide:
            return firstCamera(deviceTypes: [.builtInUltraWideCamera])
        case .telephoto:
            return firstCamera(deviceTypes: [.builtInTelephotoCamera])
        }
    }

    private func firstCamera(deviceTypes: [AVCaptureDevice.DeviceType]) -> AVCaptureDevice? {
        let discoverySession = AVCaptureDevice.DiscoverySession(
            deviceTypes: deviceTypes,
            mediaType: .video,
            position: .back
        )
        return discoverySession.devices.first
    }

    private func cameraSupportsDepth(_ camera: AVCaptureDevice) -> Bool {
        !camera.activeFormat.supportedDepthDataFormats.isEmpty
    }

    private func cameraSelectionSupportsLidarDepth(_ selection: CameraSelection) -> Bool {
        guard let lidarCamera = camera(for: .lidar) else {
            return false
        }

        switch selection {
        case .autoBack, .lidar:
            return camera(for: selection)?.uniqueID == lidarCamera.uniqueID && cameraSupportsDepth(lidarCamera)
        case .triple, .dualWide, .dual, .wide, .ultraWide, .telephoto:
            return false
        }
    }

    private func configureDepthOutputIfAvailable(for camera: AVCaptureDevice) {
        if session.outputs.contains(where: { $0 === depthOutput }) {
            session.removeOutput(depthOutput)
        }
        depthSupported = false

        guard let depthFormat = preferredDepthDataFormat(for: camera),
              session.canAddOutput(depthOutput)
        else {
            return
        }

        do {
            try camera.lockForConfiguration()
            camera.activeDepthDataFormat = depthFormat
            camera.unlockForConfiguration()
        } catch {
            depthSupported = false
            return
        }

        session.addOutput(depthOutput)
        depthOutput.alwaysDiscardsLateDepthData = true
        depthOutput.isFilteringEnabled = settings.depthMode == .qualityLidar
        depthOutput.setDelegate(self, callbackQueue: depthOutputQueue)
        if let connection = depthOutput.connection(with: .depthData) {
            connection.isEnabled = true
        }
        depthSupported = true
    }

    private func supportedDepthModes(for selectedCamera: AVCaptureDevice?) -> [CameraDepthMode] {
        guard let selectedCamera,
              let lidarCamera = camera(for: .lidar),
              selectedCamera.uniqueID == lidarCamera.uniqueID,
              cameraSupportsDepth(selectedCamera) else {
            return [.off]
        }
        return CameraDepthMode.allCases
    }

    private func preferredDepthDataFormat(for camera: AVCaptureDevice) -> AVCaptureDevice.Format? {
        guard settings.depthMode != .off else {
            return nil
        }

        let targetAspectRatio = settings.resolution.landscapeAspectRatio
        return camera.activeFormat.supportedDepthDataFormats.sorted { lhs, rhs in
            let lhsScore = depthFormatScore(lhs, targetAspectRatio: targetAspectRatio)
            let rhsScore = depthFormatScore(rhs, targetAspectRatio: targetAspectRatio)
            return depthFormatSort(lhsScore, rhsScore, mode: settings.depthMode)
        }.first
    }

    private func depthFormatSort(
        _ lhs: (supportsRequestedFPS: Bool, maxFPS: Double, aspectDelta: Double, pixelCount: Int32),
        _ rhs: (supportsRequestedFPS: Bool, maxFPS: Double, aspectDelta: Double, pixelCount: Int32),
        mode: CameraDepthMode
    ) -> Bool {
        switch mode {
        case .off:
            return false
        case .qualityLidar:
            if abs(lhs.aspectDelta - rhs.aspectDelta) > 0.001 {
                return lhs.aspectDelta < rhs.aspectDelta
            }
            if lhs.pixelCount != rhs.pixelCount {
                return lhs.pixelCount > rhs.pixelCount
            }
            if lhs.supportsRequestedFPS != rhs.supportsRequestedFPS {
                return lhs.supportsRequestedFPS
            }
            return lhs.maxFPS > rhs.maxFPS
        case .fastLidar:
            if lhs.supportsRequestedFPS != rhs.supportsRequestedFPS {
                return lhs.supportsRequestedFPS
            }
            if abs(lhs.maxFPS - rhs.maxFPS) > 0.5 {
                return lhs.maxFPS > rhs.maxFPS
            }
            if lhs.pixelCount != rhs.pixelCount {
                return lhs.pixelCount < rhs.pixelCount
            }
            return lhs.aspectDelta < rhs.aspectDelta
        case .autoLidar:
            if lhs.supportsRequestedFPS != rhs.supportsRequestedFPS {
                return lhs.supportsRequestedFPS
            }
            if !lhs.supportsRequestedFPS,
               abs(lhs.maxFPS - rhs.maxFPS) > 0.5 {
                return lhs.maxFPS > rhs.maxFPS
            }
            if abs(lhs.aspectDelta - rhs.aspectDelta) > 0.001 {
                return lhs.aspectDelta < rhs.aspectDelta
            }
            return lhs.pixelCount > rhs.pixelCount
        }
    }

    private func depthFormatScore(
        _ format: AVCaptureDevice.Format,
        targetAspectRatio: Double
    ) -> (supportsRequestedFPS: Bool, maxFPS: Double, aspectDelta: Double, pixelCount: Int32) {
        let dimensions = CMVideoFormatDescriptionGetDimensions(format.formatDescription)
        guard dimensions.width > 0, dimensions.height > 0 else {
            return (false, 0, .greatestFiniteMagnitude, 0)
        }

        let aspectRatio = Double(dimensions.width) / Double(dimensions.height)
        let maxFPS = format.videoSupportedFrameRateRanges.map(\.maxFrameRate).max() ?? 0
        let supportsRequestedFPS = supportsFrameRate(settings.fps, format: format)
        return (
            supportsRequestedFPS,
            maxFPS,
            abs(aspectRatio - targetAspectRatio),
            dimensions.width * dimensions.height
        )
    }

    private func setVideoOrientation(_ orientation: AVCaptureVideoOrientation) {
        orientationLock.lock()
        videoOrientation = orientation
        orientationLock.unlock()
    }

    private func currentVideoOrientation() -> AVCaptureVideoOrientation {
        orientationLock.lock()
        let orientation = videoOrientation
        orientationLock.unlock()
        return orientation
    }

    private func applyVideoOrientationToOutputs() {
        let orientation = currentVideoOrientation()

        if let connection = videoOutput.connection(with: .video),
           connection.isVideoOrientationSupported {
            connection.videoOrientation = orientation
        }

        if let connection = depthOutput.connection(with: .depthData),
           connection.isVideoOrientationSupported {
            connection.videoOrientation = orientation
        }
    }

    private func sanitizedSettings(_ requestedSettings: CameraSettings) -> CameraSettings {
        var sanitized = requestedSettings
        sanitized.schemaVersion = 1
        sanitized.fps = max(1, min(requestedSettings.fps, 240))
        sanitized.exposureBias = max(-8, min(requestedSettings.exposureBias, 8))
        if camera(for: sanitized.cameraSelection) == nil {
            sanitized.cameraSelection = .autoBack
        }
        if sanitized.depthMode != .off {
            if sanitized.cameraSelection == .autoBack,
               cameraSelectionSupportsLidarDepth(.autoBack) {
                sanitized.cameraSelection = .lidar
            } else if sanitized.cameraSelection != .lidar || !cameraSelectionSupportsLidarDepth(.lidar) {
                sanitized.depthMode = .off
            }
        }
        return sanitized
    }

    private func applySessionPreset(_ resolution: CameraResolution) {
        let requestedPreset = sessionPreset(for: resolution)
        if session.canSetSessionPreset(requestedPreset) {
            session.sessionPreset = requestedPreset
            return
        }

        if session.canSetSessionPreset(.hd1920x1080) {
            session.sessionPreset = .hd1920x1080
        } else if session.canSetSessionPreset(.hd1280x720) {
            session.sessionPreset = .hd1280x720
        }
    }

    private func sessionPreset(for resolution: CameraResolution) -> AVCaptureSession.Preset {
        switch resolution {
        case .hd1280x720:
            return .hd1280x720
        case .hd1920x1080:
            return .hd1920x1080
        case .hd3840x2160:
            return .hd4K3840x2160
        }
    }

    private func applyDeviceSettings(_ settings: CameraSettings, to camera: AVCaptureDevice) {
        do {
            try camera.lockForConfiguration()
            defer {
                camera.unlockForConfiguration()
            }

            applyFrameRate(settings.fps, to: camera)
            applyExposure(settings, to: camera)
            applyFocus(settings.focusMode, to: camera)
            applyWhiteBalance(settings.whiteBalanceMode, to: camera)
        } catch {
            return
        }
    }

    private func applyFrameRate(_ fps: Int, to camera: AVCaptureDevice) {
        guard supportsFrameRate(fps, camera: camera) else {
            return
        }

        let frameDuration = CMTime(value: 1, timescale: CMTimeScale(fps))
        camera.activeVideoMinFrameDuration = frameDuration
        camera.activeVideoMaxFrameDuration = frameDuration
    }

    private func supportsFrameRate(_ fps: Int, camera: AVCaptureDevice) -> Bool {
        camera.activeFormat.videoSupportedFrameRateRanges.contains { range in
            range.minFrameRate <= Double(fps) && Double(fps) <= range.maxFrameRate
        }
    }

    private func supportsFrameRate(_ fps: Int, format: AVCaptureDevice.Format) -> Bool {
        format.videoSupportedFrameRateRanges.contains { range in
            range.minFrameRate <= Double(fps) && Double(fps) <= range.maxFrameRate
        }
    }

    private func applyExposure(_ settings: CameraSettings, to camera: AVCaptureDevice) {
        switch settings.exposureMode {
        case .locked:
            if camera.isExposureModeSupported(.locked) {
                camera.exposureMode = .locked
            }
        case .continuous:
            if camera.isExposureModeSupported(.continuousAutoExposure) {
                camera.exposureMode = .continuousAutoExposure
            }
        }

        let clampedBias = min(max(Float(settings.exposureBias), camera.minExposureTargetBias), camera.maxExposureTargetBias)
        camera.setExposureTargetBias(clampedBias, completionHandler: nil)
    }

    private func applyFocus(_ mode: CameraControlMode, to camera: AVCaptureDevice) {
        switch mode {
        case .locked:
            if camera.isFocusModeSupported(.locked) {
                camera.focusMode = .locked
            }
        case .continuous:
            if camera.isFocusModeSupported(.continuousAutoFocus) {
                camera.focusMode = .continuousAutoFocus
            }
        }
    }

    private func applyWhiteBalance(_ mode: CameraControlMode, to camera: AVCaptureDevice) {
        switch mode {
        case .locked:
            if camera.isWhiteBalanceModeSupported(.locked) {
                camera.whiteBalanceMode = .locked
            }
        case .continuous:
            if camera.isWhiteBalanceModeSupported(.continuousAutoWhiteBalance) {
                camera.whiteBalanceMode = .continuousAutoWhiteBalance
            }
        }
    }
}

extension CameraCaptureService: AVCaptureVideoDataOutputSampleBufferDelegate {
    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
            return
        }

        let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        let width = CVPixelBufferGetWidth(pixelBuffer)
        let height = CVPixelBufferGetHeight(pixelBuffer)
        let intrinsics = cameraIntrinsics(from: sampleBuffer)
        let orientation = currentVideoOrientation()
        let metadata = CameraFrameMetadata(
            sessionID: sessionID,
            deviceID: deviceID,
            frameIndex: frameIndex,
            captureTimeNs: MonotonicClock.nanoseconds(from: presentationTime),
            presentationTimestamp: presentationTime.seconds,
            width: width,
            height: height,
            focalLengthPx: intrinsics.focalLength,
            principalPointPx: intrinsics.principalPoint,
            orientation: orientation.metadataValue
        )

        latestRGBFrameIndex = frameIndex
        frameIndex += 1
        frameHandler?(sampleBuffer, metadata)
    }

    private func cameraIntrinsics(from sampleBuffer: CMSampleBuffer) -> (focalLength: [Double]?, principalPoint: [Double]?) {
        guard let attachment = CMGetAttachment(
            sampleBuffer,
            key: kCMSampleBufferAttachmentKey_CameraIntrinsicMatrix,
            attachmentModeOut: nil
        ) else {
            return (nil, nil)
        }

        guard CFGetTypeID(attachment) == CFDataGetTypeID() else {
            return (nil, nil)
        }

        let matrixData = attachment as! Data
        guard matrixData.count >= MemoryLayout<matrix_float3x3>.size else {
            return (nil, nil)
        }

        let matrix = matrixData.withUnsafeBytes { pointer in
            var matrix = matrix_float3x3()
            memcpy(&matrix, pointer.baseAddress!, MemoryLayout<matrix_float3x3>.size)
            return matrix
        }

        let focalLength = [Double(matrix.columns.0.x), Double(matrix.columns.1.y)]
        let principalPoint = [Double(matrix.columns.2.x), Double(matrix.columns.2.y)]
        return (focalLength, principalPoint)
    }
}

extension CameraCaptureService: AVCaptureDepthDataOutputDelegate {
    func depthDataOutput(
        _ output: AVCaptureDepthDataOutput,
        didOutput depthData: AVDepthData,
        timestamp: CMTime,
        connection: AVCaptureConnection
    ) {
        guard let payload = makeDepthPayload(from: depthData) else {
            return
        }

        let metadata = DepthFrameMetadata(
            sessionID: sessionID,
            deviceID: deviceID,
            depthFrameIndex: depthFrameIndex,
            rgbFrameIndex: latestRGBFrameIndex,
            depthTimeNs: MonotonicClock.nanoseconds(from: timestamp),
            depthWidth: payload.width,
            depthHeight: payload.height,
            depthUnits: "meters",
            depthEncoding: "uint16_mm_little_endian",
            confidenceEncoding: nil,
            intrinsicsReference: "apple_avfoundation_rgb_intrinsics"
        )
        depthFrameIndex += 1
        let frame = DepthFrame(metadata: metadata, payload: payload.data)
        depthProcessingQueue.async { [weak self] in
            self?.depthFrameHandler?(frame)
        }
    }

    func depthDataOutput(
        _ output: AVCaptureDepthDataOutput,
        didDrop depthData: AVDepthData,
        timestamp: CMTime,
        connection: AVCaptureConnection,
        reason: AVCaptureOutput.DataDroppedReason
    ) {
    }

    private func makeDepthPayload(from depthData: AVDepthData) -> (data: Data, width: Int, height: Int)? {
        let convertedDepthData = depthData.depthDataType == kCVPixelFormatType_DepthFloat32
            ? depthData
            : depthData.converting(toDepthDataType: kCVPixelFormatType_DepthFloat32)
        let depthMap = convertedDepthData.depthDataMap
        CVPixelBufferLockBaseAddress(depthMap, .readOnly)
        defer {
            CVPixelBufferUnlockBaseAddress(depthMap, .readOnly)
        }

        guard let baseAddress = CVPixelBufferGetBaseAddress(depthMap) else {
            return nil
        }

        let width = CVPixelBufferGetWidth(depthMap)
        let height = CVPixelBufferGetHeight(depthMap)
        let bytesPerRow = CVPixelBufferGetBytesPerRow(depthMap)
        var payload = Data(count: width * height * MemoryLayout<UInt16>.size)

        payload.withUnsafeMutableBytes { outputBuffer in
            let output = outputBuffer.bindMemory(to: UInt16.self)
            for rowIndex in 0..<height {
                let row = baseAddress
                    .advanced(by: rowIndex * bytesPerRow)
                    .assumingMemoryBound(to: Float32.self)
                for columnIndex in 0..<width {
                    let meters = row[columnIndex]
                    let millimeters: UInt16
                    if meters.isFinite && meters > 0 {
                        millimeters = UInt16(min(Int((meters * 1000).rounded()), Int(UInt16.max)))
                    } else {
                        millimeters = 0
                    }
                    output[rowIndex * width + columnIndex] = millimeters.littleEndian
                }
            }
        }

        return (payload, width, height)
    }
}
