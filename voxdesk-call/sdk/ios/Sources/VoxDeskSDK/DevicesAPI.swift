import Foundation

/// Device and push-token lifecycle.
///
/// * `register` is idempotent: call it on every launch. The server scopes a row by
///   (tenant, user, platform, device id) and clears a previous revocation, which is
///   exactly what a reinstall is.
/// * `uploadToken` is write-only. The API returns a six-character hint at most.
/// * `revoke` belongs in sign-out. A device left registered keeps receiving the next
///   person's rota notifications on a shared handset.
public struct DevicesAPI {
    private let client: VoxDeskClient

    public init(client: VoxDeskClient) {
        self.client = client
    }

    public static let platformIOS = "ios"
    public static let platformAndroid = "android"

    public func list() async throws -> [Device] {
        try await client.get("/api/mobile/devices", as: DeviceListResponse.self).devices
    }

    public func register(_ request: RegisterDeviceRequest) async throws -> RegisterDeviceResponse {
        try await client.post("/api/mobile/devices", body: request, as: RegisterDeviceResponse.self)
    }

    /// Convenience for the common launch path: register and upload the APNs token.
    public func registerWithToken(
        deviceID: String,
        apnsToken: String,
        appVersion: String = "",
        osVersion: String = ""
    ) async throws -> RegisterDeviceResponse {
        try await register(
            RegisterDeviceRequest(
                platform: Self.platformIOS,
                deviceID: deviceID,
                appVersion: appVersion,
                osVersion: osVersion,
                token: apnsToken
            )
        )
    }

    public func uploadToken(deviceID: String, token: String) async throws {
        try await client.postForget("/api/mobile/devices/\(deviceID)/token", body: TokenRequest(token: token))
    }

    public func setQuietHours(deviceID: String, quietHours: QuietHours?) async throws -> Device {
        try await client.post(
            "/api/mobile/devices/\(deviceID)/quiet-hours",
            body: QuietHoursRequest(quietHours: quietHours),
            as: Device.self
        )
    }

    /// A 404 means the device is already gone, which is the state `revoke` wants.
    public func revoke(deviceID: String) async throws {
        do {
            try await client.delete("/api/mobile/devices/\(deviceID)")
        } catch let error as VoxDeskAPIError where error.isAlreadyGone {
            return
        }
    }
}
