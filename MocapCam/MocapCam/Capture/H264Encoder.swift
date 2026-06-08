import AVFoundation
import Foundation
import VideoToolbox

final class H264Encoder {
    var frameHandler: ((Data) -> Void)?

    private var compressionSession: VTCompressionSession?
    private var currentDimensions: CMVideoDimensions?
    private let encoderQueue = DispatchQueue(label: "org.freemocap.mocapcam.h264")

    func encode(sampleBuffer: CMSampleBuffer, metadata: CameraFrameMetadata) {
        encoderQueue.async {
            guard let imageBuffer = CMSampleBufferGetImageBuffer(sampleBuffer) else {
                return
            }

            do {
                try self.ensureSession(for: imageBuffer)
            } catch {
                return
            }

            guard let compressionSession = self.compressionSession else {
                return
            }

            let metadataBox = MetadataBox(metadata: metadata)
            let sourceFrameRefcon = Unmanaged.passRetained(metadataBox).toOpaque()
            let presentationTime = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
            let duration = CMSampleBufferGetDuration(sampleBuffer)

            VTCompressionSessionEncodeFrame(
                compressionSession,
                imageBuffer: imageBuffer,
                presentationTimeStamp: presentationTime,
                duration: duration.isValid ? duration : .invalid,
                frameProperties: nil,
                sourceFrameRefcon: sourceFrameRefcon,
                infoFlagsOut: nil
            )
        }
    }

    func invalidate() {
        encoderQueue.async {
            if let compressionSession = self.compressionSession {
                VTCompressionSessionCompleteFrames(compressionSession, untilPresentationTimeStamp: .invalid)
                VTCompressionSessionInvalidate(compressionSession)
            }
            self.compressionSession = nil
            self.currentDimensions = nil
        }
    }

    private func ensureSession(for imageBuffer: CVImageBuffer) throws {
        let width = Int32(CVPixelBufferGetWidth(imageBuffer))
        let height = Int32(CVPixelBufferGetHeight(imageBuffer))
        let dimensions = CMVideoDimensions(width: width, height: height)

        if compressionSession != nil, currentDimensions?.width == width, currentDimensions?.height == height {
            return
        }

        if let compressionSession {
            VTCompressionSessionCompleteFrames(compressionSession, untilPresentationTimeStamp: .invalid)
            VTCompressionSessionInvalidate(compressionSession)
        }

        var newSession: VTCompressionSession?
        let status = VTCompressionSessionCreate(
            allocator: kCFAllocatorDefault,
            width: width,
            height: height,
            codecType: kCMVideoCodecType_H264,
            encoderSpecification: nil,
            imageBufferAttributes: nil,
            compressedDataAllocator: nil,
            outputCallback: H264Encoder.compressionOutputCallback,
            refcon: Unmanaged.passUnretained(self).toOpaque(),
            compressionSessionOut: &newSession
        )

        guard status == noErr, let newSession else {
            throw NSError(domain: NSOSStatusErrorDomain, code: Int(status))
        }

        VTSessionSetProperty(newSession, key: kVTCompressionPropertyKey_RealTime, value: kCFBooleanTrue)
        VTSessionSetProperty(newSession, key: kVTCompressionPropertyKey_AllowFrameReordering, value: kCFBooleanFalse)
        VTSessionSetProperty(newSession, key: kVTCompressionPropertyKey_ProfileLevel, value: kVTProfileLevel_H264_Baseline_AutoLevel)
        VTSessionSetProperty(newSession, key: kVTCompressionPropertyKey_MaxKeyFrameInterval, value: NSNumber(value: 60))
        VTCompressionSessionPrepareToEncodeFrames(newSession)

        compressionSession = newSession
        currentDimensions = dimensions
    }

    private static let compressionOutputCallback: VTCompressionOutputCallback = { outputRefCon, sourceFrameRefCon, status, _, sampleBuffer in
        guard status == noErr, let outputRefCon, let sourceFrameRefCon, let sampleBuffer else {
            if let sourceFrameRefCon {
                Unmanaged<MetadataBox>.fromOpaque(sourceFrameRefCon).release()
            }
            return
        }

        let encoder = Unmanaged<H264Encoder>.fromOpaque(outputRefCon).takeUnretainedValue()
        let metadata = Unmanaged<MetadataBox>.fromOpaque(sourceFrameRefCon).takeRetainedValue().metadata
        encoder.emit(sampleBuffer: sampleBuffer, metadata: metadata)
    }

    private func emit(sampleBuffer: CMSampleBuffer, metadata: CameraFrameMetadata) {
        guard CMSampleBufferDataIsReady(sampleBuffer), let blockBuffer = CMSampleBufferGetDataBuffer(sampleBuffer) else {
            return
        }

        let payloadLength = CMBlockBufferGetDataLength(blockBuffer)
        guard payloadLength > 0 else {
            return
        }

        var payload = Data(count: payloadLength)
        let copyStatus = payload.withUnsafeMutableBytes { buffer in
            CMBlockBufferCopyDataBytes(blockBuffer, atOffset: 0, dataLength: payloadLength, destination: buffer.baseAddress!)
        }
        guard copyStatus == noErr else {
            return
        }

        let isKeyframe = isKeyframe(sampleBuffer)
        let formatDescription = CMSampleBufferGetFormatDescription(sampleBuffer)
        let parameterSets = isKeyframe
            ? h264ParameterSets(from: formatDescription)
            : nil
        let envelope = VideoFrameEnvelope(
            metadata: metadata,
            encodedBytes: payload.count,
            isKeyframe: isKeyframe,
            h264NalUnitHeaderLength: h264NalUnitHeaderLength(from: formatDescription),
            h264ParameterSetsBase64: parameterSets
        )

        do {
            let packet = try CapturePacket.make(type: .videoFrame, metadata: envelope, payload: payload)
            frameHandler?(packet)
        } catch {
            return
        }
    }

    private func isKeyframe(_ sampleBuffer: CMSampleBuffer) -> Bool {
        guard let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false)
            as? [[CFString: Any]],
            let first = attachments.first,
            let notSync = first[kCMSampleAttachmentKey_NotSync] as? Bool
        else {
            return true
        }
        return !notSync
    }

    private func h264ParameterSets(from formatDescription: CMFormatDescription?) -> [String]? {
        guard let formatDescription else {
            return nil
        }

        var parameterSetCount = 0
        var nalUnitHeaderLength: Int32 = 0
        let countStatus = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            formatDescription,
            parameterSetIndex: 0,
            parameterSetPointerOut: nil,
            parameterSetSizeOut: nil,
            parameterSetCountOut: &parameterSetCount,
            nalUnitHeaderLengthOut: &nalUnitHeaderLength
        )
        guard countStatus == noErr, parameterSetCount > 0 else {
            return nil
        }

        var sets: [String] = []
        for index in 0..<parameterSetCount {
            var pointer: UnsafePointer<UInt8>?
            var size = 0
            let status = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
                formatDescription,
                parameterSetIndex: index,
                parameterSetPointerOut: &pointer,
                parameterSetSizeOut: &size,
                parameterSetCountOut: nil,
                nalUnitHeaderLengthOut: nil
            )
            if status == noErr, let pointer, size > 0 {
                sets.append(Data(bytes: pointer, count: size).base64EncodedString())
            }
        }
        return sets.isEmpty ? nil : sets
    }

    private func h264NalUnitHeaderLength(from formatDescription: CMFormatDescription?) -> Int {
        guard let formatDescription else {
            return 4
        }

        var parameterSetCount = 0
        var nalUnitHeaderLength: Int32 = 4
        let status = CMVideoFormatDescriptionGetH264ParameterSetAtIndex(
            formatDescription,
            parameterSetIndex: 0,
            parameterSetPointerOut: nil,
            parameterSetSizeOut: nil,
            parameterSetCountOut: &parameterSetCount,
            nalUnitHeaderLengthOut: &nalUnitHeaderLength
        )
        return status == noErr ? Int(nalUnitHeaderLength) : 4
    }
}

private final class MetadataBox {
    let metadata: CameraFrameMetadata

    init(metadata: CameraFrameMetadata) {
        self.metadata = metadata
    }
}
