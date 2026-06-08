import CoreMotion
import Foundation

struct DeviceMotionSample: Codable {
    let timeNs: Int64
    let gravity: [Double]
    let userAcceleration: [Double]
    let rotationRate: [Double]
    let attitudeQuaternion: [Double]

    enum CodingKeys: String, CodingKey {
        case timeNs = "time_ns"
        case gravity
        case userAcceleration = "user_acceleration"
        case rotationRate = "rotation_rate"
        case attitudeQuaternion = "attitude_quaternion"
    }
}

final class DeviceMotionRecorder: @unchecked Sendable {
    private let motionManager = CMMotionManager()
    private let queue = OperationQueue()
    private let sampleQueue = DispatchQueue(label: "org.freemocap.mocapcam.motion.samples")
    private var samples: [DeviceMotionSample] = []
    private var outputURL: URL?

    var isAvailable: Bool {
        motionManager.isDeviceMotionAvailable
    }

    func startMonitoring() {
        guard motionManager.isDeviceMotionAvailable else {
            return
        }
        motionManager.deviceMotionUpdateInterval = 1.0 / 60.0
        motionManager.startDeviceMotionUpdates(to: queue) { [weak self] motion, _ in
            guard let self, let motion else {
                return
            }
            let sample = DeviceMotionSample(
                timeNs: MonotonicClock.nowNanoseconds(),
                gravity: [motion.gravity.x, motion.gravity.y, motion.gravity.z],
                userAcceleration: [
                    motion.userAcceleration.x,
                    motion.userAcceleration.y,
                    motion.userAcceleration.z
                ],
                rotationRate: [motion.rotationRate.x, motion.rotationRate.y, motion.rotationRate.z],
                attitudeQuaternion: [
                    motion.attitude.quaternion.w,
                    motion.attitude.quaternion.x,
                    motion.attitude.quaternion.y,
                    motion.attitude.quaternion.z
                ]
            )
            self.sampleQueue.async {
                if self.outputURL != nil {
                    self.samples.append(sample)
                }
            }
        }
    }

    func stopMonitoring() {
        motionManager.stopDeviceMotionUpdates()
    }

    func startSession(sessionID: String, deviceID: String) {
        sampleQueue.async {
            self.samples = []
            let root = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("FreeMoCapCapture", isDirectory: true)
                .appendingPathComponent("sessions", isDirectory: true)
                .appendingPathComponent(sessionID, isDirectory: true)
                .appendingPathComponent(deviceID, isDirectory: true)
            try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
            self.outputURL = root.appendingPathComponent("device_motion.json")
        }
    }

    func stopSession() {
        sampleQueue.async {
            guard let outputURL = self.outputURL else {
                return
            }
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            if let data = try? encoder.encode(self.samples) {
                try? data.write(to: outputURL, options: .atomic)
            }
            self.outputURL = nil
            self.samples = []
        }
    }
}
