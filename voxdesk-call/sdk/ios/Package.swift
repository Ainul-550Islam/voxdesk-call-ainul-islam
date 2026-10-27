// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "VoxDeskSDK",
    platforms: [
        .iOS(.v15),
        .macOS(.v12)
    ],
    products: [
        .library(name: "VoxDeskSDK", targets: ["VoxDeskSDK"])
    ],
    targets: [
        .target(
            name: "VoxDeskSDK",
            path: "Sources/VoxDeskSDK",
            // The privacy manifest has to ship inside the bundle, or Xcode warns at
            // upload time and App Store review asks about undeclared data use.
            resources: [.process("Resources")]
        ),
        .testTarget(
            name: "VoxDeskSDKTests",
            dependencies: ["VoxDeskSDK"],
            path: "Tests/VoxDeskSDKTests"
        )
    ]
)
