import Foundation

/// Quiet hours, validated locally and evaluated on the device's own clock.
///
/// The server is the authority: `PUSH_QUIET_HOURS_MAX_SPAN` is a deployment setting
/// and the API refuses a window it will not honour. This exists so a settings screen
/// can explain the refusal before a round trip, and so a *local* notification can be
/// suppressed correctly when the server is not in the loop.
public enum QuietHoursRules {

    /// The ceiling the API defaults to; pass the server's value when it reports one.
    public static let defaultMaxSpanHours = 12

    public enum Validation: Equatable, Sendable {
        case ok
        case invalid(reason: String)
    }

    private static let timePattern = try! NSRegularExpression(
        pattern: "^([01][0-9]|2[0-3]):([0-5][0-9])$"
    )

    public static func validate(_ quietHours: QuietHours, maxSpanHours: Int = defaultMaxSpanHours) -> Validation {
        guard matches(quietHours.start), matches(quietHours.end) else {
            return .invalid(reason: "times must be HH:MM")
        }
        guard !quietHours.timezone.isEmpty else {
            return .invalid(reason: "a timezone is required")
        }
        let span = spanMinutes(start: quietHours.start, end: quietHours.end)
        guard span != 0 else {
            return .invalid(reason: "a quiet window must have a non-zero length")
        }
        guard span <= maxSpanHours * 60 else {
            return .invalid(reason: "a quiet window may be at most \(maxSpanHours) hours")
        }
        return .ok
    }

    /// Length in minutes, wrapping through midnight: `22:00 → 07:00` is 540.
    public static func spanMinutes(start: String, end: String) -> Int {
        guard let startMinutes = minutes(start), let endMinutes = minutes(end) else { return 0 }
        return ((endMinutes - startMinutes) + 24 * 60) % (24 * 60)
    }

    /// Is `date` inside the window, read in `timezone`?
    ///
    /// A malformed window, an unknown zone, or a disabled window returns `false`:
    /// broken data must never silence somebody's notifications. The end of a window
    /// is exclusive, so 07:00 is already open.
    public static func isQuietNow(
        _ quietHours: QuietHours?,
        at date: Date = Date(),
        timeZone: TimeZone? = nil
    ) -> Bool {
        guard let quietHours, quietHours.enabled else { return false }
        guard matches(quietHours.start), matches(quietHours.end) else { return false }
        let zone = timeZone ?? TimeZone(identifier: quietHours.timezone)
        guard let zone else { return false }

        var calendar = Calendar(identifier: .gregorian)
        calendar.timeZone = zone
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        guard let hour = parts.hour, let minute = parts.minute else { return false }
        let now = hour * 60 + minute

        guard let start = minutes(quietHours.start), let end = minutes(quietHours.end) else { return false }
        guard start != end else { return false }
        return start < end ? (now >= start && now < end) : (now >= start || now < end)
    }

    static func minutes(_ value: String) -> Int? {
        guard matches(value) else { return nil }
        let pieces = value.split(separator: ":")
        guard pieces.count == 2, let hour = Int(pieces[0]), let minute = Int(pieces[1]) else { return nil }
        return hour * 60 + minute
    }

    static func matches(_ value: String) -> Bool {
        let range = NSRange(value.startIndex..<value.endIndex, in: value)
        return timePattern.firstMatch(in: value, range: range) != nil
    }
}
