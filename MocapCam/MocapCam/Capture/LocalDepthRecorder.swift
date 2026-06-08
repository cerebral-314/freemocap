import Foundation

final class LocalDepthRecorder: @unchecked Sendable {
    private let queue = DispatchQueue(label: "org.freemocap.mocapcam.depth.recorder")
    private var outputDirectory: URL?
    private var manifestEntries: [DepthFrameMetadata] = []
    private var activeSessionID: String?
    private var activeDeviceID: String?
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
            let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("FreeMoCapCapture", isDirectory: true)
                .appendingPathComponent("sessions", isDirectory: true)
                .appendingPathComponent(sessionID, isDirectory: true)
                .appendingPathComponent(deviceID, isDirectory: true)
                .appendingPathComponent("depth_uint16_mm", isDirectory: true)
            do {
                try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
                self.outputDirectory = root
            } catch {
                self.droppedFrameCount += 1
            }
        }
    }

    func append(_ frame: DepthFrame) {
        queue.async {
            guard self.activeSessionID != nil, let outputDirectory = self.outputDirectory else {
                return
            }
            let chunkID = String(format: "%06d", frame.metadata.depthFrameIndex)
            let url = outputDirectory.appendingPathComponent("\(chunkID).bin")
            do {
                try frame.payload.write(to: url, options: .atomic)
                self.manifestEntries.append(frame.metadata)
            } catch {
                self.droppedFrameCount += 1
            }
        }
    }

    func stop(completion: @escaping (Result<URL?, Error>) -> Void) {
        queue.async {
            let stoppedDirectory = self.outputDirectory
            do {
                try self.writeManifest()
                self.reset()
                completion(.success(stoppedDirectory))
            } catch {
                self.reset()
                completion(.failure(error))
            }
        }
    }

    private func writeManifest() throws {
        guard let root = outputDirectory?.deletingLastPathComponent() else {
            return
        }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(manifestEntries)
        try data.write(to: root.appendingPathComponent("depth_manifest.json"), options: .atomic)
    }

    private func reset() {
        outputDirectory = nil
        manifestEntries = []
        activeSessionID = nil
        activeDeviceID = nil
    }
}
