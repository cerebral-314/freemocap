import AVFoundation
import Combine
import Foundation
import UIKit

private struct DepthPlaneObservation {
    let normalizedRect: CGRect
    let validPixelCount: Int
    let medianMillimeters: UInt16
}

final class CaptureAppModel: ObservableObject, @unchecked Sendable {
    @Published var deviceID: String {
        didSet {
            camera.updateIdentity(sessionID: sessionID, deviceID: deviceID)
        }
    }

    @Published var sessionID: String {
        didSet {
            camera.updateIdentity(sessionID: sessionID, deviceID: deviceID)
        }
    }

    @Published private(set) var isPreviewing = false
    @Published private(set) var isRecording = false
    @Published private(set) var isDepthPreviewing = false
    @Published private(set) var currentFPS: Double = 0
    @Published private(set) var currentDepthFPS: Double = 0
    @Published private(set) var networkLabel = "Offline"
    @Published private(set) var thermalState = "Nominal"
    @Published private(set) var batteryLabel = "--"
    @Published private(set) var lastError: String?
    @Published var cameraSettings = CameraSettings.defaultSettings
    @Published private(set) var supportedCameraSettings = SupportedCameraSettings.defaultSettings
    @Published var isDepthOverlayEnabled = true
    @Published var isDepthOverlayAutoAlignmentEnabled = true {
        didSet {
            charucoAlignmentStatus = isDepthOverlayAutoAlignmentEnabled ? "Find 7x5 board" : "Manual alignment"
        }
    }
    @Published var depthOverlayOpacity = 0.52
    @Published var depthOverlayOffsetX = 0.0
    @Published var depthOverlayOffsetY = 0.0
    @Published var depthOverlayScale = 1.0
    @Published private(set) var charucoAlignmentStatus = "Find 7x5 board"
    @Published private(set) var depthOverlayImage: UIImage?

    let camera = CameraCaptureService()

    var previewSession: AVCaptureSession {
        camera.session
    }

    private let encoder = H264Encoder()
    private let recorder = LocalMovieRecorder()
    private let depthRecorder = LocalDepthRecorder()
    private let motionRecorder = DeviceMotionRecorder()
    private let socketServer = CaptureSocketServer()
    private let charucoBoardDetector = CharucoBoardDetector()
    private var statusTimer: Timer?
    private var framesInCurrentWindow = 0
    private var depthFramesInCurrentWindow = 0
    private var fpsWindowStart = Date()
    private var depthFPSWindowStart = Date()
    private var encodedFramesSent: Int64 = 0
    private var depthFramesSent: Int64 = 0
    private var scheduledStartTimeNs: Int64?
    private var lastRecordingURL: URL?
    private var depthOverlayFrameSkip = 0
    private var charucoFrameSkip = 0
    private var depthAutoAlignmentFrameSkip = 0
    private let depthOverlayStateLock = NSLock()
    private var depthOverlayOrientation: AVCaptureVideoOrientation = .landscapeRight
    private let charucoObservationLock = NSLock()
    private var latestCharucoBoardRect: CGRect?
    private var latestCharucoBoardObservationTime = Date.distantPast

    init() {
        let defaultDeviceID = DeviceIdentity.defaultDeviceID()
        self.deviceID = defaultDeviceID
        self.sessionID = DeviceIdentity.defaultSessionID()
        camera.updateIdentity(sessionID: sessionID, deviceID: defaultDeviceID)
    }

    func start() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        configureCallbacks()
        motionRecorder.startMonitoring()
        socketServer.start(serviceName: DeviceIdentity.humanReadableDeviceName)
        startStatusTimer()
        startPreview()
    }

    func stop() {
        statusTimer?.invalidate()
        statusTimer = nil
        stopRecording()
        camera.stopRunning()
        motionRecorder.stopMonitoring()
        socketServer.stop()
        encoder.invalidate()
    }

    func togglePreview() {
        isPreviewing ? stopPreview() : startPreview()
    }

    func toggleRecording() {
        isRecording ? stopRecording() : startRecording(sessionID: sessionID)
    }

    func toggleDepthPreview() {
        isDepthPreviewing.toggle()
    }

    func applyCameraSettings(_ requestedSettings: CameraSettings) {
        let previousSettings = cameraSettings
        var settings = requestedSettings
        let lidarCameraAvailable = supportedCameraSettings.cameraSelections.contains { $0.id == .lidar }

        if settings.cameraSelection != previousSettings.cameraSelection,
           settings.cameraSelection != .lidar {
            settings.depthMode = .off
        } else if settings.depthMode != .off,
                  settings.cameraSelection != .lidar {
            if lidarCameraAvailable {
                settings.cameraSelection = .lidar
            } else {
                settings.depthMode = .off
            }
        }

        cameraSettings = settings
        if settings.depthMode == .off {
            isDepthPreviewing = false
            currentDepthFPS = 0
            depthOverlayImage = nil
        }
        camera.applyCameraSettings(settings) { [weak self] in
            DispatchQueue.main.async {
                self?.refreshCameraSettings()
                self?.isDepthPreviewing = self?.camera.depthSupported == true
                if self?.camera.depthSupported != true {
                    self?.currentDepthFPS = 0
                    self?.depthOverlayImage = nil
                }
            }
        }
    }

    func updateVideoOrientation(_ orientation: AVCaptureVideoOrientation) {
        setDepthOverlayOrientation(orientation)
        camera.updateVideoOrientation(orientation)
    }

    func resetDepthOverlayAlignment() {
        depthOverlayOffsetX = 0
        depthOverlayOffsetY = 0
        depthOverlayScale = 1
    }

    func startPreview() {
        camera.requestAccessAndStart { [weak self] result in
            DispatchQueue.main.async {
                switch result {
                case .success:
                    self?.isPreviewing = true
                    self?.isDepthPreviewing = self?.camera.depthSupported == true
                    self?.refreshCameraSettings()
                    self?.lastError = nil
                case .failure(let error):
                    self?.lastError = error.localizedDescription
                }
            }
        }
    }

    func stopPreview() {
        camera.stopRunning()
        isPreviewing = false
    }

    func startRecording(sessionID requestedSessionID: String?) {
        let activeSessionID = requestedSessionID?.isEmpty == false ? requestedSessionID! : sessionID
        sessionID = activeSessionID
        recorder.startSession(sessionID: activeSessionID, deviceID: deviceID)
        depthRecorder.startSession(sessionID: activeSessionID, deviceID: deviceID)
        motionRecorder.startSession(sessionID: activeSessionID, deviceID: deviceID)
        isRecording = true
        broadcastRecordingEvent("recording_started", url: nil)
    }

    func stopRecording() {
        guard isRecording || recorder.isRecording else {
            return
        }

        isRecording = false
        depthRecorder.stop { _ in }
        motionRecorder.stopSession()
        recorder.stop { [weak self] result in
            DispatchQueue.main.async {
                switch result {
                case .success(let url):
                    self?.lastRecordingURL = url
                    self?.broadcastRecordingEvent("recording_stopped", url: url)
                case .failure(let error):
                    self?.lastError = error.localizedDescription
                    self?.socketServer.broadcastError(error.localizedDescription)
                }
            }
        }
    }

    private func configureCallbacks() {
        camera.frameHandler = { [weak self] sampleBuffer, metadata in
            self?.handleFrame(sampleBuffer: sampleBuffer, metadata: metadata)
        }

        camera.depthFrameHandler = { [weak self] frame in
            self?.handleDepthFrame(frame)
        }

        encoder.frameHandler = { [weak self] packet in
            self?.socketServer.broadcast(packet: packet)
            DispatchQueue.main.async {
                self?.encodedFramesSent += 1
            }
        }

        socketServer.commandHandler = { [weak self] command in
            DispatchQueue.main.async {
                self?.handle(command: command)
            }
        }

        socketServer.statusProvider = { [weak self] in
            self?.makeStatus()
        }

        socketServer.stateHandler = { [weak self] state in
            DispatchQueue.main.async {
                self?.networkLabel = state
            }
        }
    }

    private func handleFrame(sampleBuffer: CMSampleBuffer, metadata: CameraFrameMetadata) {
        if let startTime = scheduledStartTimeNs, metadata.captureTimeNs >= startTime {
            DispatchQueue.main.async { [weak self] in
                guard let self, self.scheduledStartTimeNs != nil else {
                    return
                }
                self.scheduledStartTimeNs = nil
                self.startRecording(sessionID: self.sessionID)
            }
        }

        if recorder.isRecording {
            recorder.append(sampleBuffer: sampleBuffer, metadata: metadata)
        }

        encoder.encode(sampleBuffer: sampleBuffer, metadata: metadata)
        updateCharucoBoardDetection(sampleBuffer: sampleBuffer)
        updateFrameRate()
    }

    private func handleDepthFrame(_ frame: DepthFrame) {
        if depthRecorder.isRecording {
            depthRecorder.append(frame)
        }

        updateDepthOverlayAutoAlignment(frame)
        updateDepthOverlay(frame)

        if camera.depthSupported {
            socketServer.broadcastDepthFrame(frame)
            DispatchQueue.main.async { [weak self] in
                self?.depthFramesSent += 1
            }
        }
        updateDepthFrameRate()
    }

    private func updateFrameRate() {
        framesInCurrentWindow += 1
        let now = Date()
        let elapsed = now.timeIntervalSince(fpsWindowStart)
        guard elapsed >= 1 else {
            return
        }

        let fps = Double(framesInCurrentWindow) / elapsed
        framesInCurrentWindow = 0
        fpsWindowStart = now

        DispatchQueue.main.async { [weak self] in
            self?.currentFPS = fps
        }
    }

    private func updateDepthFrameRate() {
        depthFramesInCurrentWindow += 1
        let now = Date()
        let elapsed = now.timeIntervalSince(depthFPSWindowStart)
        guard elapsed >= 1 else {
            return
        }

        let fps = Double(depthFramesInCurrentWindow) / elapsed
        depthFramesInCurrentWindow = 0
        depthFPSWindowStart = now

        DispatchQueue.main.async { [weak self] in
            self?.currentDepthFPS = fps
        }
    }

    private func handle(command: CaptureCommand) {
        switch command.command {
        case "start_preview":
            startPreview()
        case "stop_preview":
            stopPreview()
        case "start_depth_preview":
            isDepthPreviewing = true
        case "stop_depth_preview":
            isDepthPreviewing = camera.depthSupported
        case "start_recording":
            applyCameraSettings(command)
            startRecording(sessionID: command.sessionID)
        case "arm_recording":
            applyCameraSettings(command)
            if let commandSessionID = command.sessionID, !commandSessionID.isEmpty {
                sessionID = commandSessionID
            }
            scheduledStartTimeNs = command.startAtDeviceTimeNs ?? command.startAtServerTimeNs
            broadcastRecordingEvent("recording_armed", url: nil)
        case "stop_recording":
            stopRecording()
        case "set_device_name":
            if let newDeviceID = command.deviceID, !newDeviceID.isEmpty {
                deviceID = newDeviceID
            }
        case "set_camera_settings":
            applyCameraSettings(command)
        case "ping":
            let receiveTime = MonotonicClock.nowNanoseconds()
            socketServer.broadcastClockSync(
                ClockSyncReply(
                    requestID: command.requestID,
                    serverTimeSendNs: command.serverTimeSendNs,
                    deviceTimeReceiveNs: receiveTime,
                    deviceTimeReplyNs: MonotonicClock.nowNanoseconds(),
                    deviceID: deviceID,
                    sessionID: sessionID
                )
            )
            socketServer.broadcastStatus(makeStatus())
        case "list_local_files":
            broadcastLocalFileManifest(requestID: command.requestID)
        case "download_local_file":
            sendLocalFileChunk(command: command)
        default:
            let message = "Unsupported command: \(command.command)"
            lastError = message
            socketServer.broadcastError(message)
        }
    }

    private func startStatusTimer() {
        statusTimer?.invalidate()
        statusTimer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            guard let self else {
                return
            }
            self.refreshDeviceStateLabels()
            self.socketServer.broadcastStatus(self.makeStatus())
        }
    }

    private func refreshDeviceStateLabels() {
        thermalState = ProcessInfo.processInfo.thermalState.displayName
        let batteryLevel = UIDevice.current.batteryLevel
        batteryLabel = batteryLevel >= 0 ? "\(Int((batteryLevel * 100).rounded()))%" : "--"
    }

    private func makeStatus() -> DeviceStatus {
        DeviceStatus(
            deviceID: deviceID,
            deviceName: DeviceIdentity.humanReadableDeviceName,
            sessionID: sessionID,
            previewActive: isPreviewing,
            recordingActive: isRecording,
            fps: currentFPS,
            encodedFramesSent: encodedFramesSent,
            depthFramesSent: depthFramesSent,
            droppedFrames: recorder.droppedFrames,
            droppedDepthFrames: depthRecorder.droppedFrames,
            depthSupported: camera.depthSupported,
            depthPreviewActive: isDepthPreviewing,
            connectedClients: socketServer.connectedClientCount,
            servicePort: socketServer.currentPort,
            batteryPercent: currentBatteryPercent(),
            batteryState: UIDevice.current.batteryState.displayName,
            thermalState: ProcessInfo.processInfo.thermalState.displayName,
            availableStorageBytes: availableStorageBytes(),
            lastRecordingURL: lastRecordingURL?.absoluteString,
            cameraSettings: cameraSettings,
            supportedCameraSettings: supportedCameraSettings
        )
    }

    private func currentBatteryPercent() -> Int? {
        let batteryLevel = UIDevice.current.batteryLevel
        guard batteryLevel >= 0 else {
            return nil
        }
        return Int((batteryLevel * 100).rounded())
    }

    private func availableStorageBytes() -> Int64? {
        do {
            let url = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            let values = try url.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
            return values.volumeAvailableCapacityForImportantUsage
        } catch {
            return nil
        }
    }

    private func broadcastRecordingEvent(_ event: String, url: URL?) {
        let payload = RecordingEvent(
            event: event,
            sessionID: sessionID,
            deviceID: deviceID,
            url: url?.absoluteString,
            timeNs: MonotonicClock.nowNanoseconds()
        )
        socketServer.broadcastRecordingEvent(payload)
    }

    private func applyCameraSettings(_ command: CaptureCommand) {
        var settings = cameraSettings

        if let commandSettings = command.cameraSettings {
            settings = commandSettings
        }
        if let requestedFPS = command.requestedFPS {
            settings.fps = requestedFPS
        }
        if let requestedResolution = command.requestedResolution,
           let resolution = CameraResolution(rawValue: requestedResolution) {
            settings.resolution = resolution
        }
        if let exposureMode = command.exposureMode,
           let mode = CameraControlMode(rawValue: exposureMode) {
            settings.exposureMode = mode
        }
        if let exposureBias = command.exposureBias {
            settings.exposureBias = exposureBias
        }
        if let focusMode = command.focusMode,
           let mode = CameraControlMode(rawValue: focusMode) {
            settings.focusMode = mode
        }
        if let whiteBalanceMode = command.whiteBalanceMode,
           let mode = CameraControlMode(rawValue: whiteBalanceMode) {
            settings.whiteBalanceMode = mode
        }

        if let lockExposure = command.lockExposure {
            settings.exposureMode = lockExposure ? .locked : .continuous
        }
        if let lockFocus = command.lockFocus {
            settings.focusMode = lockFocus ? .locked : .continuous
        }
        if let lockWhiteBalance = command.lockWhiteBalance {
            settings.whiteBalanceMode = lockWhiteBalance ? .locked : .continuous
        }

        applyCameraSettings(settings)
    }

    private func refreshCameraSettings() {
        supportedCameraSettings = camera.supportedCameraSettings()
        cameraSettings = camera.currentCameraSettings()
    }

    private func updateCharucoBoardDetection(sampleBuffer: CMSampleBuffer) {
        guard isDepthOverlayEnabled, isDepthOverlayAutoAlignmentEnabled, camera.depthSupported else {
            return
        }

        charucoFrameSkip = (charucoFrameSkip + 1) % 10
        guard charucoFrameSkip == 0 else {
            return
        }

        if let observation = charucoBoardDetector.detect(in: sampleBuffer),
           observation.normalizedRect.width > 0,
           observation.normalizedRect.height > 0 {
            charucoObservationLock.lock()
            latestCharucoBoardRect = observation.normalizedRect
            latestCharucoBoardObservationTime = Date()
            charucoObservationLock.unlock()

            DispatchQueue.main.async { [weak self] in
                self?.charucoAlignmentStatus = "7x5 board locked"
            }
            return
        }

        if latestCharucoBoardRect(maxAge: 1.5) == nil {
            charucoObservationLock.lock()
            latestCharucoBoardRect = nil
            charucoObservationLock.unlock()
            DispatchQueue.main.async { [weak self] in
                self?.charucoAlignmentStatus = "Find 7x5 board"
            }
        }
    }

    private func updateDepthOverlayAutoAlignment(_ frame: DepthFrame) {
        guard isDepthOverlayEnabled, isDepthOverlayAutoAlignmentEnabled else {
            return
        }

        depthAutoAlignmentFrameSkip = (depthAutoAlignmentFrameSkip + 1) % 2
        guard depthAutoAlignmentFrameSkip == 0,
              let boardRect = latestCharucoBoardRect(maxAge: 2.0) else {
            return
        }

        guard let depthPlane = detectDepthPlane(in: frame, around: boardRect) else {
            DispatchQueue.main.async { [weak self] in
                self?.charucoAlignmentStatus = "7x5 board locked; LiDAR plane missing"
            }
            return
        }

        let displayedDepthRect = overlayDisplayRect(
            forRawDepthRect: depthPlane.normalizedRect,
            scale: depthOverlayScale,
            offsetX: depthOverlayOffsetX,
            offsetY: depthOverlayOffsetY
        )
        let boardCenter = boardRect.center
        let depthCenter = displayedDepthRect.center
        let errorX = Double(boardCenter.x - depthCenter.x)
        let errorY = Double(boardCenter.y - depthCenter.y)
        guard errorX.isFinite, errorY.isFinite else {
            return
        }

        let targetScaleX = boardRect.width / max(depthPlane.normalizedRect.width, 0.001)
        let targetScaleY = boardRect.height / max(depthPlane.normalizedRect.height, 0.001)
        let targetScale = Self.clamp(Double((targetScaleX + targetScaleY) * 0.5), min: 0.86, max: 1.20)
        let confidence = min(max(Double(depthPlane.validPixelCount) / 1800.0, 0), 1)
        let offsetSmoothing = 0.08 + 0.14 * confidence
        let scaleSmoothing = 0.04 + 0.08 * confidence
        let depthMeters = Double(depthPlane.medianMillimeters) / 1000.0

        DispatchQueue.main.async { [weak self] in
            guard let self else {
                return
            }

            self.depthOverlayOffsetX = Self.clamp(
                self.depthOverlayOffsetX + errorX * offsetSmoothing,
                min: -0.18,
                max: 0.18
            )
            self.depthOverlayOffsetY = Self.clamp(
                self.depthOverlayOffsetY + errorY * offsetSmoothing,
                min: -0.18,
                max: 0.18
            )
            self.depthOverlayScale = Self.clamp(
                self.depthOverlayScale + (targetScale - self.depthOverlayScale) * scaleSmoothing,
                min: 0.86,
                max: 1.20
            )
            self.charucoAlignmentStatus = String(format: "7x5 board + LiDAR synced %.2fm", depthMeters)
        }
    }

    private func latestCharucoBoardRect(maxAge: TimeInterval) -> CGRect? {
        charucoObservationLock.lock()
        let rect = latestCharucoBoardRect
        let observationTime = latestCharucoBoardObservationTime
        charucoObservationLock.unlock()

        guard Date().timeIntervalSince(observationTime) <= maxAge else {
            return nil
        }
        return rect
    }

    private func detectDepthPlane(in frame: DepthFrame, around boardRect: CGRect) -> DepthPlaneObservation? {
        let width = frame.metadata.depthWidth
        let height = frame.metadata.depthHeight
        guard width > 0, height > 0, frame.payload.count >= width * height * MemoryLayout<UInt16>.size else {
            return nil
        }

        let crop = depthOverlayCropRect(width: width, height: height)
        guard crop.width > 0, crop.height > 0 else {
            return nil
        }

        let expectedRawRect = inverseOverlayDisplayRect(
            boardRect,
            scale: depthOverlayScale,
            offsetX: depthOverlayOffsetX,
            offsetY: depthOverlayOffsetY
        )
        let searchPaddingX = max(CGFloat(0.035), expectedRawRect.width * 0.25)
        let searchPaddingY = max(CGFloat(0.035), expectedRawRect.height * 0.25)
        let searchRect = expectedRawRect.paddedBy(x: searchPaddingX, y: searchPaddingY)
        guard let searchPixelRect = pixelRect(for: searchRect, crop: crop) else {
            return nil
        }

        guard let medianMillimeters = medianDepthMillimeters(
            in: searchPixelRect,
            frame: frame,
            width: width
        ) else {
            return nil
        }

        let tolerance = max(65, Int(Double(medianMillimeters) * 0.075))
        var minLocalX = crop.width
        var minLocalY = crop.height
        var maxLocalX = 0
        var maxLocalY = 0
        var validPixelCount = 0
        let searchWidth = max(1, searchPixelRect.maxX - searchPixelRect.minX)
        let searchHeight = max(1, searchPixelRect.maxY - searchPixelRect.minY)
        let sampleStep = max(1, min(searchWidth, searchHeight) / 220)

        frame.payload.withUnsafeBytes { inputBuffer in
            let input = inputBuffer.bindMemory(to: UInt16.self)
            for y in stride(from: searchPixelRect.minY, to: searchPixelRect.maxY, by: sampleStep) {
                for x in stride(from: searchPixelRect.minX, to: searchPixelRect.maxX, by: sampleStep) {
                    let rawValue = UInt16(littleEndian: input[y * width + x])
                    guard rawValue > 0, abs(Int(rawValue) - Int(medianMillimeters)) <= tolerance else {
                        continue
                    }

                    let localX = x - crop.x
                    let localY = y - crop.y
                    minLocalX = min(minLocalX, localX)
                    minLocalY = min(minLocalY, localY)
                    maxLocalX = max(maxLocalX, localX + 1)
                    maxLocalY = max(maxLocalY, localY + 1)
                    validPixelCount += 1
                }
            }
        }

        guard validPixelCount >= 12, minLocalX < maxLocalX, minLocalY < maxLocalY else {
            return nil
        }

        let normalizedRect = CGRect(
            x: CGFloat(minLocalX) / CGFloat(crop.width),
            y: CGFloat(minLocalY) / CGFloat(crop.height),
            width: CGFloat(maxLocalX - minLocalX) / CGFloat(crop.width),
            height: CGFloat(maxLocalY - minLocalY) / CGFloat(crop.height)
        ).clampedToNormalizedUnit()

        guard normalizedRect.width > 0.01, normalizedRect.height > 0.01 else {
            return nil
        }

        return DepthPlaneObservation(
            normalizedRect: normalizedRect,
            validPixelCount: validPixelCount,
            medianMillimeters: medianMillimeters
        )
    }

    private func medianDepthMillimeters(
        in rect: (minX: Int, minY: Int, maxX: Int, maxY: Int),
        frame: DepthFrame,
        width: Int
    ) -> UInt16? {
        let sampleWidth = max(1, rect.maxX - rect.minX)
        let sampleHeight = max(1, rect.maxY - rect.minY)
        let sampleStep = max(1, min(sampleWidth, sampleHeight) / 90)
        var values: [UInt16] = []
        values.reserveCapacity((sampleWidth / sampleStep + 1) * (sampleHeight / sampleStep + 1))

        frame.payload.withUnsafeBytes { inputBuffer in
            let input = inputBuffer.bindMemory(to: UInt16.self)
            for y in stride(from: rect.minY, to: rect.maxY, by: sampleStep) {
                for x in stride(from: rect.minX, to: rect.maxX, by: sampleStep) {
                    let millimeters = UInt16(littleEndian: input[y * width + x])
                    if millimeters >= 150, millimeters <= 8_000 {
                        values.append(millimeters)
                    }
                }
            }
        }

        guard values.count >= 16 else {
            return nil
        }
        values.sort()
        return values[values.count / 2]
    }

    private func pixelRect(
        for normalizedRect: CGRect,
        crop: (x: Int, y: Int, width: Int, height: Int)
    ) -> (minX: Int, minY: Int, maxX: Int, maxY: Int)? {
        let rect = normalizedRect.clampedToNormalizedUnit()
        let minX = crop.x + min(max(Int(floor(rect.minX * CGFloat(crop.width))), 0), crop.width - 1)
        let minY = crop.y + min(max(Int(floor(rect.minY * CGFloat(crop.height))), 0), crop.height - 1)
        let maxX = crop.x + min(max(Int(ceil(rect.maxX * CGFloat(crop.width))), 1), crop.width)
        let maxY = crop.y + min(max(Int(ceil(rect.maxY * CGFloat(crop.height))), 1), crop.height)

        guard minX < maxX, minY < maxY else {
            return nil
        }
        return (minX, minY, maxX, maxY)
    }

    private func overlayDisplayRect(forRawDepthRect rect: CGRect, scale: Double, offsetX: Double, offsetY: Double) -> CGRect {
        let scale = CGFloat(max(scale, 0.001))
        let offsetX = CGFloat(offsetX)
        let offsetY = CGFloat(offsetY)
        return CGRect(
            x: 0.5 + (rect.minX - 0.5) * scale + offsetX,
            y: 0.5 + (rect.minY - 0.5) * scale + offsetY,
            width: rect.width * scale,
            height: rect.height * scale
        )
    }

    private func inverseOverlayDisplayRect(_ rect: CGRect, scale: Double, offsetX: Double, offsetY: Double) -> CGRect {
        let scale = CGFloat(max(scale, 0.001))
        let offsetX = CGFloat(offsetX)
        let offsetY = CGFloat(offsetY)
        return CGRect(
            x: 0.5 + ((rect.minX - offsetX) - 0.5) / scale,
            y: 0.5 + ((rect.minY - offsetY) - 0.5) / scale,
            width: rect.width / scale,
            height: rect.height / scale
        ).clampedToNormalizedUnit()
    }

    private static func clamp(_ value: Double, min minimum: Double, max maximum: Double) -> Double {
        Swift.min(Swift.max(value, minimum), maximum)
    }

    private func updateDepthOverlay(_ frame: DepthFrame) {
        guard isDepthOverlayEnabled else {
            return
        }

        depthOverlayFrameSkip = (depthOverlayFrameSkip + 1) % 3
        guard depthOverlayFrameSkip == 0 else {
            return
        }

        guard let image = makeDepthOverlayImage(frame) else {
            return
        }

        DispatchQueue.main.async { [weak self] in
            self?.depthOverlayImage = image
        }
    }

    private func makeDepthOverlayImage(_ frame: DepthFrame) -> UIImage? {
        let width = frame.metadata.depthWidth
        let height = frame.metadata.depthHeight
        guard width > 0, height > 0, frame.payload.count >= width * height * MemoryLayout<UInt16>.size else {
            return nil
        }

        let crop = depthOverlayCropRect(width: width, height: height)
        guard crop.width > 0, crop.height > 0 else {
            return nil
        }

        var rgba = Data(count: crop.width * crop.height * 4)
        rgba.withUnsafeMutableBytes { outputBuffer in
            frame.payload.withUnsafeBytes { inputBuffer in
                let input = inputBuffer.bindMemory(to: UInt16.self)
                let output = outputBuffer.bindMemory(to: UInt8.self)
                for outputRow in 0..<crop.height {
                    let sourceRow = crop.y + outputRow
                    for outputColumn in 0..<crop.width {
                        let sourceColumn = crop.x + outputColumn
                        let sourceIndex = sourceRow * width + sourceColumn
                        let outputIndex = (outputRow * crop.width + outputColumn) * 4
                        let millimeters = UInt16(littleEndian: input[sourceIndex])
                        guard millimeters > 0 else {
                            output[outputIndex] = 0
                            output[outputIndex + 1] = 0
                            output[outputIndex + 2] = 0
                            output[outputIndex + 3] = 0
                            continue
                        }

                        let normalized = min(max((Double(millimeters) - 350.0) / 3650.0, 0), 1)
                        let alpha = UInt8(190)
                        let color = depthColor(normalized)
                        output[outputIndex] = UInt8(Double(color.red) * Double(alpha) / 255.0)
                        output[outputIndex + 1] = UInt8(Double(color.green) * Double(alpha) / 255.0)
                        output[outputIndex + 2] = UInt8(Double(color.blue) * Double(alpha) / 255.0)
                        output[outputIndex + 3] = alpha
                    }
                }
            }
        }

        let colorSpace = CGColorSpaceCreateDeviceRGB()
        guard let provider = CGDataProvider(data: rgba as CFData),
              let cgImage = CGImage(
                width: crop.width,
                height: crop.height,
                bitsPerComponent: 8,
                bitsPerPixel: 32,
                bytesPerRow: crop.width * 4,
                space: colorSpace,
                bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.premultipliedLast.rawValue),
                provider: provider,
                decode: nil,
                shouldInterpolate: true,
                intent: .defaultIntent
              )
        else {
            return nil
        }
        return UIImage(cgImage: cgImage)
    }

    private func setDepthOverlayOrientation(_ orientation: AVCaptureVideoOrientation) {
        depthOverlayStateLock.lock()
        depthOverlayOrientation = orientation
        depthOverlayStateLock.unlock()
    }

    private func currentDepthOverlayOrientation() -> AVCaptureVideoOrientation {
        depthOverlayStateLock.lock()
        let orientation = depthOverlayOrientation
        depthOverlayStateLock.unlock()
        return orientation
    }

    private func depthOverlayCropRect(width: Int, height: Int) -> (x: Int, y: Int, width: Int, height: Int) {
        let targetAspectRatio = depthOverlayTargetAspectRatio()
        let sourceAspectRatio = Double(width) / Double(height)

        if sourceAspectRatio > targetAspectRatio {
            let cropWidth = max(1, min(width, Int((Double(height) * targetAspectRatio).rounded())))
            return ((width - cropWidth) / 2, 0, cropWidth, height)
        }

        if sourceAspectRatio < targetAspectRatio {
            let cropHeight = max(1, min(height, Int((Double(width) / targetAspectRatio).rounded())))
            return (0, (height - cropHeight) / 2, width, cropHeight)
        }

        return (0, 0, width, height)
    }

    private func depthOverlayTargetAspectRatio() -> Double {
        let landscapeAspectRatio = cameraSettings.resolution.landscapeAspectRatio
        switch currentDepthOverlayOrientation() {
        case .portrait, .portraitUpsideDown:
            return 1.0 / landscapeAspectRatio
        case .landscapeLeft, .landscapeRight:
            return landscapeAspectRatio
        @unknown default:
            return landscapeAspectRatio
        }
    }

    private func depthColor(_ value: Double) -> (red: UInt8, green: UInt8, blue: UInt8) {
        let clamped = min(max(value, 0), 1)
        let red = UInt8(max(0, min(255, 255.0 * (1.4 - abs(clamped * 4 - 3)))))
        let green = UInt8(max(0, min(255, 255.0 * (1.4 - abs(clamped * 4 - 2)))))
        let blue = UInt8(max(0, min(255, 255.0 * (1.4 - abs(clamped * 4 - 1)))))
        return (red, green, blue)
    }

    private func broadcastLocalFileManifest(requestID: String?) {
        let files = localCaptureRoot().flatMap { root -> [LocalFileEntry]? in
            guard let enumerator = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey, .contentModificationDateKey]) else {
                return nil
            }
            return enumerator.compactMap { item in
                guard let url = item as? URL, !url.hasDirectoryPath else {
                    return nil
                }
                do {
                    let values = try url.resourceValues(forKeys: [.fileSizeKey, .contentModificationDateKey])
                    return LocalFileEntry(
                        path: relativePath(url, from: root),
                        sizeBytes: Int64(values.fileSize ?? 0),
                        modifiedTimeNs: values.contentModificationDate.map { Int64($0.timeIntervalSince1970 * 1_000_000_000) }
                    )
                } catch {
                    return nil
                }
            }
        } ?? []

        socketServer.broadcastLocalFileManifest(
            LocalFileManifest(
                requestID: requestID,
                sessionID: sessionID,
                deviceID: deviceID,
                files: files
            )
        )
    }

    private func sendLocalFileChunk(command: CaptureCommand) {
        guard let requestedPath = command.filePath,
              let root = localCaptureRoot(),
              let fileURL = safeLocalFileURL(relativePath: requestedPath, root: root)
        else {
            socketServer.broadcastError("Invalid local file request")
            return
        }

        do {
            let fileData = try Data(contentsOf: fileURL)
            let offset = max(command.offset ?? 0, 0)
            let requestedLength = max(command.length ?? 262_144, 1)
            guard offset < fileData.count else {
                socketServer.broadcastError("Local file offset is beyond file size")
                return
            }
            let end = min(offset + requestedLength, fileData.count)
            let payload = fileData.subdata(in: offset..<end)
            socketServer.broadcastLocalFileChunk(
                LocalFileChunk(
                    requestID: command.requestID,
                    sessionID: sessionID,
                    deviceID: deviceID,
                    filePath: requestedPath,
                    offset: offset,
                    payloadBytes: payload.count,
                    fileSizeBytes: Int64(fileData.count),
                    isFinal: end >= fileData.count
                ),
                payload: payload
            )
        } catch {
            socketServer.broadcastError(error.localizedDescription)
        }
    }

    private func localCaptureRoot() -> URL? {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first?
            .appendingPathComponent("FreeMoCapCapture", isDirectory: true)
    }

    private func safeLocalFileURL(relativePath: String, root: URL) -> URL? {
        guard !relativePath.contains("..") else {
            return nil
        }
        let candidate = root.appendingPathComponent(relativePath)
        let rootPath = root.standardizedFileURL.path
        let candidatePath = candidate.standardizedFileURL.path
        guard candidatePath.hasPrefix(rootPath) else {
            return nil
        }
        return candidate
    }

    private func relativePath(_ url: URL, from root: URL) -> String {
        let rootPath = root.standardizedFileURL.path
        let filePath = url.standardizedFileURL.path
        guard filePath.hasPrefix(rootPath) else {
            return url.lastPathComponent
        }
        return String(filePath.dropFirst(rootPath.count)).trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    }
}

private extension ProcessInfo.ThermalState {
    var displayName: String {
        switch self {
        case .nominal:
            return "Nominal"
        case .fair:
            return "Fair"
        case .serious:
            return "Serious"
        case .critical:
            return "Critical"
        @unknown default:
            return "Unknown"
        }
    }
}

private extension UIDevice.BatteryState {
    var displayName: String {
        switch self {
        case .unknown:
            return "unknown"
        case .unplugged:
            return "unplugged"
        case .charging:
            return "charging"
        case .full:
            return "full"
        @unknown default:
            return "unknown"
        }
    }
}

private extension CGRect {
    var center: CGPoint {
        CGPoint(x: midX, y: midY)
    }

    func paddedBy(x paddingX: CGFloat, y paddingY: CGFloat) -> CGRect {
        CGRect(
            x: minX - paddingX,
            y: minY - paddingY,
            width: width + paddingX * 2,
            height: height + paddingY * 2
        ).clampedToNormalizedUnit()
    }

    func clampedToNormalizedUnit() -> CGRect {
        let clampedMinX = min(max(minX, 0), 1)
        let clampedMinY = min(max(minY, 0), 1)
        let clampedMaxX = min(max(maxX, 0), 1)
        let clampedMaxY = min(max(maxY, 0), 1)
        return CGRect(
            x: clampedMinX,
            y: clampedMinY,
            width: max(0, clampedMaxX - clampedMinX),
            height: max(0, clampedMaxY - clampedMinY)
        )
    }
}
