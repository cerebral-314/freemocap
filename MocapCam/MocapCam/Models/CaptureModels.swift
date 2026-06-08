import Foundation

enum CameraResolution: String, Codable, CaseIterable, Equatable, Hashable, Identifiable {
    case hd1280x720 = "1280x720"
    case hd1920x1080 = "1920x1080"
    case hd3840x2160 = "3840x2160"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .hd1280x720:
            return "720p"
        case .hd1920x1080:
            return "1080p"
        case .hd3840x2160:
            return "4K"
        }
    }

    var landscapeAspectRatio: Double {
        switch self {
        case .hd1280x720:
            return 1280.0 / 720.0
        case .hd1920x1080:
            return 1920.0 / 1080.0
        case .hd3840x2160:
            return 3840.0 / 2160.0
        }
    }
}

enum CameraControlMode: String, Codable, CaseIterable, Equatable, Hashable, Identifiable {
    case continuous
    case locked

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .continuous:
            return "Auto"
        case .locked:
            return "Locked"
        }
    }
}

enum CameraSelection: String, Codable, CaseIterable, Equatable, Hashable, Identifiable {
    case autoBack = "auto_back"
    case lidar = "lidar"
    case triple = "triple"
    case dualWide = "dual_wide"
    case dual = "dual"
    case wide = "wide"
    case ultraWide = "ultra_wide"
    case telephoto = "telephoto"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .autoBack:
            return "Auto Back"
        case .lidar:
            return "LiDAR"
        case .triple:
            return "Triple"
        case .dualWide:
            return "Dual Wide"
        case .dual:
            return "Dual"
        case .wide:
            return "Wide"
        case .ultraWide:
            return "Ultra Wide"
        case .telephoto:
            return "Telephoto"
        }
    }
}

struct CameraSelectionOption: Codable, Equatable, Identifiable {
    let id: CameraSelection
    let displayName: String
    let depthSupported: Bool

    enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case depthSupported = "depth_supported"
    }
}

enum CameraDepthMode: String, Codable, CaseIterable, Equatable, Hashable, Identifiable {
    case off
    case autoLidar = "auto_lidar"
    case fastLidar = "fast_lidar"
    case qualityLidar = "quality_lidar"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .off:
            return "Off"
        case .autoLidar:
            return "Auto LiDAR"
        case .fastLidar:
            return "Fast LiDAR"
        case .qualityLidar:
            return "Quality LiDAR"
        }
    }
}

struct CameraSettings: Codable, Equatable {
    var schemaVersion: Int
    var cameraSelection: CameraSelection
    var resolution: CameraResolution
    var fps: Int
    var exposureMode: CameraControlMode
    var exposureBias: Double
    var focusMode: CameraControlMode
    var whiteBalanceMode: CameraControlMode
    var depthMode: CameraDepthMode

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case cameraSelection = "camera_selection"
        case resolution
        case fps
        case exposureMode = "exposure_mode"
        case exposureBias = "exposure_bias"
        case focusMode = "focus_mode"
        case whiteBalanceMode = "white_balance_mode"
        case depthMode = "depth_mode"
    }

    init(
        schemaVersion: Int,
        cameraSelection: CameraSelection = .autoBack,
        resolution: CameraResolution,
        fps: Int,
        exposureMode: CameraControlMode,
        exposureBias: Double,
        focusMode: CameraControlMode,
        whiteBalanceMode: CameraControlMode,
        depthMode: CameraDepthMode
    ) {
        self.schemaVersion = schemaVersion
        self.cameraSelection = cameraSelection
        self.resolution = resolution
        self.fps = fps
        self.exposureMode = exposureMode
        self.exposureBias = exposureBias
        self.focusMode = focusMode
        self.whiteBalanceMode = whiteBalanceMode
        self.depthMode = depthMode
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        cameraSelection = try container.decodeIfPresent(CameraSelection.self, forKey: .cameraSelection) ?? .autoBack
        resolution = try container.decodeIfPresent(CameraResolution.self, forKey: .resolution) ?? .hd1920x1080
        fps = try container.decodeIfPresent(Int.self, forKey: .fps) ?? 30
        exposureMode = try container.decodeIfPresent(CameraControlMode.self, forKey: .exposureMode) ?? .continuous
        exposureBias = try container.decodeIfPresent(Double.self, forKey: .exposureBias) ?? 0
        focusMode = try container.decodeIfPresent(CameraControlMode.self, forKey: .focusMode) ?? .continuous
        whiteBalanceMode = try container.decodeIfPresent(CameraControlMode.self, forKey: .whiteBalanceMode) ?? .continuous
        depthMode = try container.decodeIfPresent(CameraDepthMode.self, forKey: .depthMode) ?? .autoLidar
    }

    static let defaultSettings = CameraSettings(
        schemaVersion: 1,
        cameraSelection: .autoBack,
        resolution: .hd1920x1080,
        fps: 30,
        exposureMode: .continuous,
        exposureBias: 0,
        focusMode: .continuous,
        whiteBalanceMode: .continuous,
        depthMode: .autoLidar
    )
}

struct SupportedCameraSettings: Codable, Equatable {
    let cameraSelections: [CameraSelectionOption]
    let resolutions: [CameraResolution]
    let fps: [Int]
    let exposureModes: [CameraControlMode]
    let focusModes: [CameraControlMode]
    let whiteBalanceModes: [CameraControlMode]
    let depthModes: [CameraDepthMode]

    enum CodingKeys: String, CodingKey {
        case cameraSelections = "camera_selections"
        case resolutions
        case fps
        case exposureModes = "exposure_modes"
        case focusModes = "focus_modes"
        case whiteBalanceModes = "white_balance_modes"
        case depthModes = "depth_modes"
    }

    static let defaultSettings = SupportedCameraSettings(
        cameraSelections: [
            CameraSelectionOption(id: .autoBack, displayName: CameraSelection.autoBack.displayName, depthSupported: true)
        ],
        resolutions: CameraResolution.allCases,
        fps: [24, 30, 60],
        exposureModes: CameraControlMode.allCases,
        focusModes: CameraControlMode.allCases,
        whiteBalanceModes: CameraControlMode.allCases,
        depthModes: CameraDepthMode.allCases
    )
}

struct CameraFrameMetadata: Codable {
    let sessionID: String
    let deviceID: String
    let frameIndex: Int64
    let captureTimeNs: Int64
    let presentationTimestamp: Double
    let width: Int
    let height: Int
    let focalLengthPx: [Double]?
    let principalPointPx: [Double]?
    let orientation: String

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case deviceID = "device_id"
        case frameIndex = "frame_index"
        case captureTimeNs = "capture_time_ns"
        case presentationTimestamp = "presentation_timestamp"
        case width
        case height
        case focalLengthPx = "focal_length_px"
        case principalPointPx = "principal_point_px"
        case orientation
    }
}

struct DepthFrameMetadata: Codable {
    let sessionID: String
    let deviceID: String
    let depthFrameIndex: Int64
    let rgbFrameIndex: Int64?
    let depthTimeNs: Int64
    let depthWidth: Int
    let depthHeight: Int
    let depthUnits: String
    let depthEncoding: String
    let confidenceEncoding: String?
    let intrinsicsReference: String

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case deviceID = "device_id"
        case depthFrameIndex = "depth_frame_index"
        case rgbFrameIndex = "rgb_frame_index"
        case depthTimeNs = "depth_time_ns"
        case depthWidth = "depth_width"
        case depthHeight = "depth_height"
        case depthUnits = "depth_units"
        case depthEncoding = "depth_encoding"
        case confidenceEncoding = "confidence_encoding"
        case intrinsicsReference = "intrinsics_reference"
    }
}

struct DepthFrame {
    let metadata: DepthFrameMetadata
    let payload: Data
}

struct CaptureCommand: Codable {
    let command: String
    let sessionID: String?
    let deviceID: String?
    let startAtServerTimeNs: Int64?
    let startAtDeviceTimeNs: Int64?
    let requestID: String?
    let serverTimeSendNs: Int64?
    let filePath: String?
    let offset: Int?
    let length: Int?
    let requestedFPS: Int?
    let requestedResolution: String?
    let cameraSettings: CameraSettings?
    let exposureMode: String?
    let exposureBias: Double?
    let focusMode: String?
    let whiteBalanceMode: String?
    let lockExposure: Bool?
    let lockFocus: Bool?
    let lockWhiteBalance: Bool?

    enum CodingKeys: String, CodingKey {
        case command
        case sessionID = "session_id"
        case deviceID = "device_id"
        case startAtServerTimeNs = "start_at_server_time_ns"
        case startAtDeviceTimeNs = "start_at_device_time_ns"
        case requestID = "request_id"
        case serverTimeSendNs = "server_time_send_ns"
        case filePath = "file_path"
        case offset
        case length
        case requestedFPS = "requested_fps"
        case requestedResolution = "requested_resolution"
        case cameraSettings = "camera_settings"
        case exposureMode = "exposure_mode"
        case exposureBias = "exposure_bias"
        case focusMode = "focus_mode"
        case whiteBalanceMode = "white_balance_mode"
        case lockExposure = "lock_exposure"
        case lockFocus = "lock_focus"
        case lockWhiteBalance = "lock_white_balance"
    }
}

struct DeviceStatus: Codable {
    let deviceID: String
    let deviceName: String
    let sessionID: String
    let previewActive: Bool
    let recordingActive: Bool
    let fps: Double
    let encodedFramesSent: Int64
    let depthFramesSent: Int64
    let droppedFrames: Int64
    let droppedDepthFrames: Int64
    let depthSupported: Bool
    let depthPreviewActive: Bool
    let connectedClients: Int
    let servicePort: UInt16?
    let batteryPercent: Int?
    let batteryState: String
    let thermalState: String
    let availableStorageBytes: Int64?
    let lastRecordingURL: String?
    let cameraSettings: CameraSettings
    let supportedCameraSettings: SupportedCameraSettings

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case deviceName = "device_name"
        case sessionID = "session_id"
        case previewActive = "preview_active"
        case recordingActive = "recording_active"
        case fps
        case encodedFramesSent = "encoded_frames_sent"
        case depthFramesSent = "depth_frames_sent"
        case droppedFrames = "dropped_frames"
        case droppedDepthFrames = "dropped_depth_frames"
        case depthSupported = "depth_supported"
        case depthPreviewActive = "depth_preview_active"
        case connectedClients = "connected_clients"
        case servicePort = "service_port"
        case batteryPercent = "battery_percent"
        case batteryState = "battery_state"
        case thermalState = "thermal_state"
        case availableStorageBytes = "available_storage_bytes"
        case lastRecordingURL = "last_recording_url"
        case cameraSettings = "camera_settings"
        case supportedCameraSettings = "supported_camera_settings"
    }
}

struct FrameManifestDocument: Codable {
    let sessionID: String
    let deviceID: String
    let rgbFile: String
    let frames: [FrameManifestEntry]

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case deviceID = "device_id"
        case rgbFile = "rgb_file"
        case frames
    }
}

struct FrameManifestEntry: Codable {
    let rgbFrameIndex: Int64
    let rgbTimeNs: Int64
    let depthFrameIndex: Int64?
    let depthTimeNs: Int64?
    let depthChunkID: String?
    let presentationTimestamp: Double
    let width: Int
    let height: Int
    let dropped: Bool

    enum CodingKeys: String, CodingKey {
        case rgbFrameIndex = "rgb_frame_index"
        case rgbTimeNs = "rgb_time_ns"
        case depthFrameIndex = "depth_frame_index"
        case depthTimeNs = "depth_time_ns"
        case depthChunkID = "depth_chunk_id"
        case presentationTimestamp = "presentation_timestamp"
        case width
        case height
        case dropped
    }
}

struct RecordingEvent: Codable {
    let event: String
    let sessionID: String
    let deviceID: String
    let url: String?
    let timeNs: Int64

    enum CodingKeys: String, CodingKey {
        case event
        case sessionID = "session_id"
        case deviceID = "device_id"
        case url
        case timeNs = "time_ns"
    }
}

struct ClockSyncReply: Codable {
    let requestID: String?
    let serverTimeSendNs: Int64?
    let deviceTimeReceiveNs: Int64
    let deviceTimeReplyNs: Int64
    let deviceID: String
    let sessionID: String

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case serverTimeSendNs = "server_time_send_ns"
        case deviceTimeReceiveNs = "device_time_receive_ns"
        case deviceTimeReplyNs = "device_time_reply_ns"
        case deviceID = "device_id"
        case sessionID = "session_id"
    }
}

struct LocalFileEntry: Codable {
    let path: String
    let sizeBytes: Int64
    let modifiedTimeNs: Int64?

    enum CodingKeys: String, CodingKey {
        case path
        case sizeBytes = "size_bytes"
        case modifiedTimeNs = "modified_time_ns"
    }
}

struct LocalFileManifest: Codable {
    let requestID: String?
    let sessionID: String
    let deviceID: String
    let files: [LocalFileEntry]

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case sessionID = "session_id"
        case deviceID = "device_id"
        case files
    }
}

struct LocalFileChunk: Codable {
    let requestID: String?
    let sessionID: String
    let deviceID: String
    let filePath: String
    let offset: Int
    let payloadBytes: Int
    let fileSizeBytes: Int64
    let isFinal: Bool

    enum CodingKeys: String, CodingKey {
        case requestID = "request_id"
        case sessionID = "session_id"
        case deviceID = "device_id"
        case filePath = "file_path"
        case offset
        case payloadBytes = "payload_bytes"
        case fileSizeBytes = "file_size_bytes"
        case isFinal = "is_final"
    }
}
