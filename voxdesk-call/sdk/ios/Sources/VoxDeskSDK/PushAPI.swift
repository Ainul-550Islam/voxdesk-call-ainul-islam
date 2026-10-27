import Foundation

/// Push status and self-test.
///
/// `test` exists so "notifications aren't arriving" is two taps rather than an
/// investigation: it sends to the caller's own devices and returns the per-device
/// outcome, including the provider's error code.
public struct PushAPI {
    private let client: VoxDeskClient

    public init(client: VoxDeskClient) {
        self.client = client
    }

    public func deliveries(limit: Int? = nil, status: String? = nil) async throws -> DeliveryList {
        var query: [String: String] = [:]
        if let limit { query["limit"] = String(limit) }
        if let status { query["status"] = status }
        return try await client.get("/api/mobile/deliveries", query: query, as: DeliveryList.self)
    }

    /// Provider capability. Booleans only — the API never returns credentials.
    public func providers() async throws -> ProviderStatus {
        try await client.get("/api/mobile/providers", as: ProviderStatus.self)
    }

    /// `urgent` bypasses quiet hours and defaults to `false`, so a self-test cannot
    /// wake somebody up by accident.
    public func test(title: String = "", body: String = "", urgent: Bool = false) async throws -> PushTestResult {
        try await client.post(
            "/api/mobile/push/test",
            body: PushTestRequest(template: "push_test", title: title, body: body, urgent: urgent),
            as: PushTestResult.self
        )
    }
}

/// The caller's own rota — the reason the app exists for most agents.
///
/// No `userID` parameter anywhere: the person comes from the session, so an app
/// cannot ask for a colleague's shifts even by accident.
public struct RotaAPI {
    private let client: VoxDeskClient

    public init(client: VoxDeskClient) {
        self.client = client
    }

    public func myShifts(start: String? = nil, end: String? = nil) async throws -> MyShiftsResponse {
        var query: [String: String] = [:]
        if let start { query["start"] = start }
        if let end { query["end"] = end }
        return try await client.get("/api/wfm/my-shifts", query: query, as: MyShiftsResponse.self)
    }
}

/// Entry point: one client, three APIs.
///
/// ```swift
/// let voxdesk = VoxDesk(
///     VoxDeskClient(baseURL: "https://acme.voxdesk.example") { await session.accessToken() }
/// )
/// let device = try await voxdesk.devices.registerWithToken(deviceID: id, apnsToken: hex)
/// ```
public struct VoxDesk {
    public let client: VoxDeskClient
    public let devices: DevicesAPI
    public let push: PushAPI
    public let rota: RotaAPI

    public init(_ client: VoxDeskClient) {
        self.client = client
        self.devices = DevicesAPI(client: client)
        self.push = PushAPI(client: client)
        self.rota = RotaAPI(client: client)
    }
}
