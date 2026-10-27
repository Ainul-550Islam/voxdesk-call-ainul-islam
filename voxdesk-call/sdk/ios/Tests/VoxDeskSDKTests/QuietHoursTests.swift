import XCTest
@testable import VoxDeskSDK

/// Quiet hours are the only SDK logic that runs without a server, so they are the
/// only logic tested in full here. Each case is a question support has actually been
/// asked: "why was my phone silent at 10am?" / "why did it buzz at 2am?"
final class QuietHoursTests: XCTestCase {

    private func window(_ start: String, _ end: String, zone: String = "Asia/Dhaka", enabled: Bool = true) -> QuietHours {
        QuietHours(start: start, end: end, timezone: zone, enabled: enabled)
    }

    private func date(_ iso: String) -> Date {
        ISO8601DateFormatter().date(from: iso)!
    }

    func testSpanWrapsThroughMidnight() {
        XCTAssertEqual(QuietHoursRules.spanMinutes(start: "22:00", end: "07:00"), 540)
        XCTAssertEqual(QuietHoursRules.spanMinutes(start: "09:00", end: "17:00"), 480)
        XCTAssertEqual(QuietHoursRules.spanMinutes(start: "09:00", end: "09:00"), 0)
    }

    func testAcceptsAWindowThatCrossesMidnight() {
        XCTAssertEqual(QuietHoursRules.validate(window("22:00", "07:00")), .ok)
    }

    func testRejectsAZeroLengthWindow() {
        XCTAssertEqual(
            QuietHoursRules.validate(window("09:00", "09:00")),
            .invalid(reason: "a quiet window must have a non-zero length")
        )
    }

    func testRejectsAWindowLongerThanTheCeiling() {
        XCTAssertEqual(
            QuietHoursRules.validate(window("06:00", "20:00")),
            .invalid(reason: "a quiet window may be at most 12 hours")
        )
    }

    func testHonoursADeploymentSpecificCeiling() {
        XCTAssertEqual(QuietHoursRules.validate(window("06:00", "20:00"), maxSpanHours: 14), .ok)
    }

    func testRejectsMalformedTimesAndABlankZone() {
        if case .ok = QuietHoursRules.validate(window("9am", "07:00")) {
            XCTFail("a malformed start time must be refused")
        }
        if case .ok = QuietHoursRules.validate(window("22:00", "07:00", zone: "")) {
            XCTFail("a blank timezone must be refused")
        }
    }

    func testIsQuietInsideAWrappingWindowInTheDeviceZone() {
        let quiet = window("22:00", "07:00")
        // Dhaka is UTC+6 with no DST.
        XCTAssertTrue(QuietHoursRules.isQuietNow(quiet, at: date("2026-10-26T20:00:00Z")))  // 02:00
        XCTAssertTrue(QuietHoursRules.isQuietNow(quiet, at: date("2026-10-26T17:30:00Z")))  // 23:30
        XCTAssertFalse(QuietHoursRules.isQuietNow(quiet, at: date("2026-10-26T04:30:00Z"))) // 10:30
    }

    func testTheEndOfAWindowIsAlreadyOpen() {
        // 01:00Z is exactly 07:00 in Dhaka.
        XCTAssertFalse(QuietHoursRules.isQuietNow(window("22:00", "07:00"), at: date("2026-10-26T01:00:00Z")))
    }

    func testADisabledOrMalformedWindowNeverSilencesNotifications() {
        XCTAssertFalse(
            QuietHoursRules.isQuietNow(window("22:00", "07:00", enabled: false), at: date("2026-10-26T17:30:00Z"))
        )
        XCTAssertFalse(
            QuietHoursRules.isQuietNow(window("nope", "07:00"), at: date("2026-10-26T17:30:00Z"))
        )
        XCTAssertFalse(QuietHoursRules.isQuietNow(nil, at: date("2026-10-26T17:30:00Z")))
    }

    func testAnUnknownZoneIsOpenRatherThanSilent() {
        XCTAssertFalse(
            QuietHoursRules.isQuietNow(
                window("22:00", "07:00", zone: "Mars/Olympus"),
                at: date("2026-10-26T17:30:00Z")
            )
        )
    }

    func testAnExplicitZoneOverridesTheStoredOne() {
        // 12:00Z is 18:00 in Dhaka (outside 22:00-07:00) but 14:00 in Honolulu (also
        // outside) — so check a window that is inside in one and outside in the other.
        let quiet = window("09:00", "17:00", zone: "Asia/Dhaka")
        XCTAssertTrue(QuietHoursRules.isQuietNow(quiet, at: date("2026-10-26T06:00:00Z")))  // 12:00 Dhaka
        XCTAssertFalse(
            QuietHoursRules.isQuietNow(quiet, at: date("2026-10-26T06:00:00Z"), timeZone: TimeZone(identifier: "Pacific/Honolulu"))
        )
    }
}
