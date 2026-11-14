// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "HarborPhoneCore", platforms: [.iOS(.v17), .macOS(.v14)], products: [
    .library(name: "HarborCore", targets: ["HarborCore"]),
    .library(name: "HarborPhoneKit", targets: ["HarborPhoneKit"]),
    .executable(name: "workflow20-ios", targets: ["WorkflowContractRunner"])
], targets: [
    .target(name: "HarborCore"),
    .target(name: "HarborPhoneKit"),
    .executableTarget(name: "WorkflowContractRunner", dependencies: ["HarborCore"]),
    .testTarget(name: "HarborCoreTests", dependencies: ["HarborCore"]),
    .testTarget(name: "HarborPhoneKitTests", dependencies: ["HarborPhoneKit"])
])
