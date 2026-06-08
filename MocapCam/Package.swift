// swift-tools-version: 6.0

import PackageDescription

let package = Package(
    name: "MocapCam",
    platforms: [
        .iOS(.v16)
    ],
    products: [
        .library(name: "MocapCam", targets: ["MocapCam"])
    ],
    targets: [
        .target(
            name: "MocapCam",
            path: "MocapCam",
            exclude: [
                "Info.plist"
            ],
            resources: [
                .process("Assets.xcassets"),
                .process("Resources")
            ]
        )
    ]
)
