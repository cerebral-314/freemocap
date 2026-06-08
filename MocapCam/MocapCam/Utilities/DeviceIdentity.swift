import CoreMedia
import Foundation
import UIKit

enum DeviceIdentity {
    static func defaultDeviceID() -> String {
        let prefix = UIDevice.current.userInterfaceIdiom == .pad ? "ipad" : "iphone"
        let uuid = UIDevice.current.identifierForVendor?.uuidString.lowercased() ?? UUID().uuidString.lowercased()
        return "\(prefix)_\(uuid.prefix(8))"
    }

    static func defaultSessionID() -> String {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd_HH-mm-ss"
        return "\(formatter.string(from: Date()))_mocapcam"
    }

    static var humanReadableDeviceName: String {
        UIDevice.current.name
    }
}

enum MonotonicClock {
    static func nowNanoseconds() -> Int64 {
        let hostTime = CMClockGetTime(CMClockGetHostTimeClock())
        return nanoseconds(from: hostTime)
    }

    static func nanoseconds(from time: CMTime) -> Int64 {
        guard time.isValid && !time.seconds.isNaN && !time.seconds.isInfinite else {
            return 0
        }
        return CMTimeConvertScale(time, timescale: 1_000_000_000, method: .roundHalfAwayFromZero).value
    }
}
