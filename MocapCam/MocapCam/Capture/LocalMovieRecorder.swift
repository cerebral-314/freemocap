import AVFoundation
import Foundation

final class LocalMovieRecorder {
    enum RecorderError: LocalizedError {
        case writerUnavailable
        case cannotAddInput

        var errorDescription: String? {
            switch self {
            case .writerUnavailable:
                return "The local movie writer is not available."
            case .cannotAddInput:
                return "The local movie writer input could not be added."
            }
        }
    }

    private let queue = DispatchQueue(label: "org.freemocap.mocapcam.recorder")
    private var writer: AVAssetWriter?
    private var input: AVAssetWriterInput?
    private var manifestEntries: [FrameManifestEntry] = []
    private var outputDirectory: URL?
    private var outputMovieURL: URL?
    private var activeSessionID: String?
    private var activeDeviceID: String?
    private var didStartSession = false
    private var droppedFrameCount: Int64 = 0

    var droppedFrames: Int64 {
        queue.sync {
            droppedFrameCount
        }
    }

    var isRecording: Bool {
        queue.sync {
            activeSessionID != nil
        }
    }

    func startSession(sessionID: String, deviceID: String) {
        queue.async {
            self.reset()
            self.activeSessionID = sessionID
            self.activeDeviceID = deviceID
        }
    }

    func append(sampleBuffer: CMSampleBuffer, metadata: CameraFrameMetadata) {
        queue.async {
            guard self.activeSessionID != nil else {
                return
            }

            do {
                if self.writer == nil {
                    try self.prepareWriter(sampleBuffer: sampleBuffer, metadata: metadata)
                }

                guard let writer = self.writer, let input = self.input else {
                    self.droppedFrameCount += 1
                    return
                }

                if !self.didStartSession {
                    writer.startWriting()
                    writer.startSession(atSourceTime: CMSampleBufferGetPresentationTimeStamp(sampleBuffer))
                    self.didStartSession = true
                }

                if input.isReadyForMoreMediaData {
                    input.append(sampleBuffer)
                    self.manifestEntries.append(
                        FrameManifestEntry(
                            rgbFrameIndex: metadata.frameIndex,
                            rgbTimeNs: metadata.captureTimeNs,
                            depthFrameIndex: nil,
                            depthTimeNs: nil,
                            depthChunkID: nil,
                            presentationTimestamp: metadata.presentationTimestamp,
                            width: metadata.width,
                            height: metadata.height,
                            dropped: false
                        )
                    )
                } else {
                    self.droppedFrameCount += 1
                    self.manifestEntries.append(
                        FrameManifestEntry(
                            rgbFrameIndex: metadata.frameIndex,
                            rgbTimeNs: metadata.captureTimeNs,
                            depthFrameIndex: nil,
                            depthTimeNs: nil,
                            depthChunkID: nil,
                            presentationTimestamp: metadata.presentationTimestamp,
                            width: metadata.width,
                            height: metadata.height,
                            dropped: true
                        )
                    )
                }
            } catch {
                self.droppedFrameCount += 1
            }
        }
    }

    func stop(completion: @escaping (Result<URL, Error>) -> Void) {
        queue.async {
            guard let writer = self.writer, let input = self.input, let outputDirectory = self.outputDirectory else {
                self.reset()
                completion(.failure(RecorderError.writerUnavailable))
                return
            }

            let stoppedSessionID = self.activeSessionID
            let stoppedDeviceID = self.activeDeviceID
            self.activeSessionID = nil
            input.markAsFinished()
            writer.finishWriting {
                self.queue.async {
                    do {
                        try self.writeManifest(sessionID: stoppedSessionID, deviceID: stoppedDeviceID)
                        self.reset(keepOutputDirectory: true)
                        completion(.success(outputDirectory))
                    } catch {
                        self.reset()
                        completion(.failure(error))
                    }
                }
            }
        }
    }

    private func prepareWriter(sampleBuffer: CMSampleBuffer, metadata: CameraFrameMetadata) throws {
        let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("FreeMoCapCapture", isDirectory: true)
            .appendingPathComponent("sessions", isDirectory: true)
            .appendingPathComponent(metadata.sessionID, isDirectory: true)
            .appendingPathComponent(metadata.deviceID, isDirectory: true)

        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)

        let movieURL = root.appendingPathComponent("rgb.mov")
        if FileManager.default.fileExists(atPath: movieURL.path) {
            try FileManager.default.removeItem(at: movieURL)
        }

        let writer = try AVAssetWriter(outputURL: movieURL, fileType: .mov)
        let outputSettings: [String: Any] = [
            AVVideoCodecKey: AVVideoCodecType.h264,
            AVVideoWidthKey: metadata.width,
            AVVideoHeightKey: metadata.height
        ]
        let input = AVAssetWriterInput(mediaType: .video, outputSettings: outputSettings)
        input.expectsMediaDataInRealTime = true

        guard writer.canAdd(input) else {
            throw RecorderError.cannotAddInput
        }
        writer.add(input)

        self.writer = writer
        self.input = input
        self.outputDirectory = root
        self.outputMovieURL = movieURL
    }

    private func writeManifest(sessionID: String?, deviceID: String?) throws {
        guard let outputDirectory, let sessionID, let deviceID else {
            return
        }

        let document = FrameManifestDocument(
            sessionID: sessionID,
            deviceID: deviceID,
            rgbFile: "rgb.mov",
            frames: manifestEntries
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(document)
        try data.write(to: outputDirectory.appendingPathComponent("frame_manifest.json"), options: .atomic)
    }

    private func reset(keepOutputDirectory: Bool = false) {
        writer = nil
        input = nil
        manifestEntries = []
        activeSessionID = nil
        activeDeviceID = nil
        didStartSession = false
        if !keepOutputDirectory {
            outputDirectory = nil
            outputMovieURL = nil
        }
    }
}
