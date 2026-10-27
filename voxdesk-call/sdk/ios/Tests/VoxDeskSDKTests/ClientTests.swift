import XCTest
@testable import VoxDeskSDK

/// URL building and wire mapping — the parts that fail silently.
final class ClientTests: XCTestCase {

    private lazy var client = VoxDeskClient(baseURL: "https://acme.example/")

    func testTrailingSlashInTheBaseURLNeverDoublesUp() {
        XCTAssertEqual(client.url(for: "/api/mobile/devices").absoluteString, "https://acme.example/api/mobile/devices")
        XCTAssertEqual(client.url(for: "api/mobile/devices").absoluteString, "https://acme.example/api/mobile/devices")
    }

    func testQueryParametersAreSortedAndBlanksDropped() {
        let url = client.url(
            for: "/api/mobile/deliveries",
            query: ["status": "failed", "limit": "20", "q": ""]
        )
        XCTAssertEqual(url.absoluteString, "https://acme.example/api/mobile/deliveries?limit=20&status=failed")
    }

    func testATimezoneOffsetInAQueryValueIsPercentEncoded() {
        let url = client.url(for: "/api/wfm/my-shifts", query: ["start": "2026-10-05T00:00:00+06:00"])
        XCTAssertTrue(url.absoluteString.contains("start=2026-10-05T00:00:00%2B06:00"), url.absoluteString)
    }

    func testIdempotencyKeysAreUniquePerCall() {
        let keys = Set((0..<50).map { _ in VoxDeskClient.newIdempotencyKey() })
        XCTAssertEqual(keys.count, 50)
    }

    func testWireNamesFollowTheAPI() throws {
        let request = RegisterDeviceRequest(
            platform: DevicesAPI.platformIOS,
            deviceID: "hardware-abc",
            appVersion: "1.0.0",
            osVersion: "18.1",
            token: "apns-token"
        )
        let data = try JSONEncoder().encode(request)
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertTrue(json.contains("\"device_id\":\"hardware-abc\""))
        XCTAssertTrue(json.contains("\"app_version\":\"1.0.0\""))
        XCTAssertTrue(json.contains("\"os_version\":\"18.1\""))
        XCTAssertTrue(json.contains("\"push_enabled\":true"))
        XCTAssertFalse(json.contains("user_id"))
    }

    func testClearingQuietHoursSendsAnExplicitNull() throws {
        let data = try JSONEncoder().encode(QuietHoursRequest(quietHours: nil))
        XCTAssertEqual(String(decoding: data, as: UTF8.self), "{\"quiet_hours\":null}")
    }

    func testAnEncodingErrorIsTypedRatherThanCrashing() {
        XCTAssertTrue(VoxDeskAPIError(status: 429, code: "rate_limited", message: "slow down").isPermanent == false)
        XCTAssertTrue(VoxDeskAPIError(status: 403, code: "permission_denied", message: "no").isPermanent)
        XCTAssertTrue(VoxDeskAPIError(status: 404, code: "device_not_found", message: "gone").isAlreadyGone)
    }

    func testADeviceDecodesWithQuietHours() throws {
        let payload = """
        {"id":"dev-1","user_id":"u-1","platform":"ios","device_id":"d-1",
         "quiet_hours":{"start":"22:00","end":"07:00","timezone":"Asia/Dhaka","enabled":true},
         "tokens":[]}
        """
        let device = try JSONDecoder().decode(Device.self, from: Data(payload.utf8))
        XCTAssertEqual(device.deviceID, "d-1")
        XCTAssertEqual(device.quietHours?.start, "22:00")
        XCTAssertEqual(device.pushEnabled, true)
    }
}
