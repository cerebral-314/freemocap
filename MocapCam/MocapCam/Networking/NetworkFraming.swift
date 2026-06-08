import Foundation

enum CapturePacketType: UInt8 {
    case deviceStatus = 1
    case videoFrame = 2
    case recordingEvent = 3
    case error = 4
    case depthFrame = 5
    case clockSync = 6
    case localFileManifest = 7
    case localFileChunk = 8
    case captureQuality = 9
}

struct DeviceStatusEnvelope: Codable {
    let packetType = "device_status"
    let schemaVersion = 1
    let status: DeviceStatus

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case status
    }
}

struct VideoFrameEnvelope: Codable {
    let packetType = "video_frame"
    let schemaVersion = 1
    let codec = "h264_avcc"
    let metadata: CameraFrameMetadata
    let encodedBytes: Int
    let isKeyframe: Bool
    let h264NalUnitHeaderLength: Int
    let h264ParameterSetsBase64: [String]?

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case codec
        case metadata
        case encodedBytes = "encoded_bytes"
        case isKeyframe = "is_keyframe"
        case h264NalUnitHeaderLength = "h264_nal_unit_header_length"
        case h264ParameterSetsBase64 = "h264_parameter_sets_base64"
    }
}

struct DepthFrameEnvelope: Codable {
    let packetType = "depth_frame"
    let schemaVersion = 1
    let metadata: DepthFrameMetadata
    let payloadBytes: Int

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case metadata
        case payloadBytes = "payload_bytes"
    }
}

struct RecordingEventEnvelope: Codable {
    let packetType = "recording_event"
    let schemaVersion = 1
    let event: RecordingEvent

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case event
    }
}

struct ErrorEnvelope: Codable {
    let packetType = "error"
    let schemaVersion = 1
    let message: String

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case message
    }
}

struct ClockSyncEnvelope: Codable {
    let packetType = "clock_sync"
    let schemaVersion = 1
    let sync: ClockSyncReply

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case sync
    }
}

struct LocalFileManifestEnvelope: Codable {
    let packetType = "local_file_manifest"
    let schemaVersion = 1
    let manifest: LocalFileManifest

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case manifest
    }
}

struct LocalFileChunkEnvelope: Codable {
    let packetType = "local_file_chunk"
    let schemaVersion = 1
    let chunk: LocalFileChunk

    enum CodingKeys: String, CodingKey {
        case packetType = "packet_type"
        case schemaVersion = "schema_version"
        case chunk
    }
}

enum CapturePacket {
    static func make<Metadata: Encodable>(
        type: CapturePacketType,
        metadata: Metadata,
        payload: Data = Data()
    ) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let metadataBytes = try encoder.encode(metadata)

        var packet = Data()
        packet.append(contentsOf: [0x46, 0x4D, 0x43, 0x31])
        packet.append(contentsOf: [0x01])
        packet.append(contentsOf: [type.rawValue])
        packet.appendBigEndian(UInt16(0))
        packet.appendBigEndian(UInt32(metadataBytes.count))
        packet.appendBigEndian(UInt32(payload.count))
        packet.append(metadataBytes)
        packet.append(payload)
        return packet
    }
}

extension Data {
    mutating func appendBigEndian<T: FixedWidthInteger>(_ value: T) {
        var bigEndianValue = value.bigEndian
        Swift.withUnsafeBytes(of: &bigEndianValue) { buffer in
            append(contentsOf: buffer)
        }
    }
}
