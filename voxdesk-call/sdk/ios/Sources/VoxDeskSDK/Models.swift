import Foundation

// MARK: - Wire types
//
// Every property maps to the API's snake_case name with an explicit `CodingKeys`.
// Explicit rather than `.convertFromSnakeCase` because a key-strategy change on one
// type silently changes every type: `device_id` and `deviceID` are one refactor away
// from a bug where a device registers with an empty identifier.

public struct Device: Codable, Equatable, Sendable {
    public let id: String
    public let userID: String
    public let platform: String
    public let deviceID: String
    public let appVersion: String
    public let osVersion: String
    public let pushEnabled: Bool
    public let quietHours: QuietHours?
    public let lastSeenAt: String?
    public let revokedAt: String?
    public let createdAt: String?
    public let tokens: [PushTokenSummary]

    public init(
        id: String,
        userID: String,
        platform: String,
        deviceID: String,
        appVersion: String = "",
        osVersion: String = "",
        pushEnabled: Bool = true,
        quietHours: QuietHours? = nil,
        lastSeenAt: String? = nil,
        revokedAt: String? = nil,
        createdAt: String? = nil,
        tokens: [PushTokenSummary] = []
    ) {
        self.id = id
        self.userID = userID
        self.platform = platform
        self.deviceID = deviceID
        self.appVersion = appVersion
        self.osVersion = osVersion
        self.pushEnabled = pushEnabled
        self.quietHours = quietHours
        self.lastSeenAt = lastSeenAt
        self.revokedAt = revokedAt
        self.createdAt = createdAt
        self.tokens = tokens
    }

    enum CodingKeys: String, CodingKey {
        case id
        case userID = "user_id"
        case platform
        case deviceID = "device_id"
        case appVersion = "app_version"
        case osVersion = "os_version"
        case pushEnabled = "push_enabled"
        case quietHours = "quiet_hours"
        case lastSeenAt = "last_seen_at"
        case revokedAt = "revoked_at"
        case createdAt = "created_at"
        case tokens
    }
}

public struct QuietHours: Codable, Equatable, Sendable {
    /// `HH:MM` in `timezone`. An end before the start wraps through midnight.
    public let start: String
    public let end: String
    public let timezone: String
    public let enabled: Bool

    public init(start: String, end: String, timezone: String, enabled: Bool = true) {
        self.start = start
        self.end = end
        self.timezone = timezone
        self.enabled = enabled
    }
}

public struct PushTokenSummary: Codable, Equatable, Sendable {
    public let id: String
    public let platform: String
    /// Last six characters only — the API never returns a whole token.
    public let hint: String
    public let active: Bool
    public let failureCount: Int
    public let lastError: String
    public let invalidatedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case platform
        case hint
        case active
        case failureCount = "failure_count"
        case lastError = "last_error"
        case invalidatedAt = "invalidated_at"
    }
}

public struct RegisterDeviceRequest: Codable, Sendable {
    public let platform: String
    public let deviceID: String
    public let appVersion: String
    public let osVersion: String
    public let pushEnabled: Bool
    public let quietHours: QuietHours?
    public let token: String?

    public init(
        platform: String,
        deviceID: String,
        appVersion: String = "",
        osVersion: String = "",
        pushEnabled: Bool = true,
        quietHours: QuietHours? = nil,
        token: String? = nil
    ) {
        self.platform = platform
        self.deviceID = deviceID
        self.appVersion = appVersion
        self.osVersion = osVersion
        self.pushEnabled = pushEnabled
        self.quietHours = quietHours
        self.token = token
    }

    enum CodingKeys: String, CodingKey {
        case platform
        case deviceID = "device_id"
        case appVersion = "app_version"
        case osVersion = "os_version"
        case pushEnabled = "push_enabled"
        case quietHours = "quiet_hours"
        case token
    }
}

public struct RegisterDeviceResponse: Codable, Sendable {
    public let id: String
    public let userID: String
    public let platform: String
    public let deviceID: String
    public let created: Bool
    public let quietHours: QuietHours?

    enum CodingKeys: String, CodingKey {
        case id
        case userID = "user_id"
        case platform
        case deviceID = "device_id"
        case created
        case quietHours = "quiet_hours"
    }
}

struct DeviceListResponse: Decodable, Sendable {
    let devices: [Device]
    let count: Int
}

struct TokenRequest: Encodable, Sendable {
    let token: String
}

struct QuietHoursRequest: Encodable, Sendable {
    /// Encoded as an explicit `null` when nil — that is how the API is told to clear
    /// the window. Omitting the key means "leave it alone".
    let quietHours: QuietHours?

    enum CodingKeys: String, CodingKey {
        case quietHours = "quiet_hours"
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(quietHours, forKey: .quietHours)
    }
}

public struct Delivery: Codable, Sendable {
    public let id: String
    public let deviceID: String
    public let template: String
    public let provider: String
    /// `queued`, `sent`, `failed` or `dropped`.
    public let status: String
    public let providerMessageID: String
    public let errorCode: String
    public let errorReason: String
    public let sentAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case deviceID = "device_id"
        case template
        case provider
        case status
        case providerMessageID = "provider_message_id"
        case errorCode = "error_code"
        case errorReason = "error_reason"
        case sentAt = "sent_at"
    }
}

public struct DeliveryList: Codable, Sendable {
    public let deliveries: [Delivery]
    public let summary: [String: Int]
}

public struct PushTestRequest: Encodable, Sendable {
    public let template: String
    public let title: String
    public let body: String
    public let urgent: Bool

    public init(template: String = "push_test", title: String = "", body: String = "", urgent: Bool = false) {
        self.template = template
        self.title = title
        self.body = body
        self.urgent = urgent
    }
}

public struct PushAttempt: Codable, Sendable {
    public let deviceID: String
    public let platform: String
    public let status: String
    public let dedupeKey: String
    public let errorCode: String
    public let errorReason: String

    enum CodingKeys: String, CodingKey {
        case deviceID = "device_id"
        case platform
        case status
        case dedupeKey = "dedupe_key"
        case errorCode = "error_code"
        case errorReason = "error_reason"
    }
}

public struct PushTestResult: Codable, Sendable {
    public let attempts: [PushAttempt]
    public let sent: Int
    public let failed: Int
    public let suppressed: Int
}

public struct ProviderCapability: Codable, Sendable {
    public let enabled: Bool
    public let configured: Bool
    public let sandbox: Bool
}

public struct ProviderStatus: Codable, Sendable {
    public let fcm: ProviderCapability
    public let apns: ProviderCapability
    public let quietHoursMaxSpanHours: Int
    public let tokenGCDays: Int

    enum CodingKeys: String, CodingKey {
        case fcm
        case apns
        case quietHoursMaxSpanHours = "quiet_hours_max_span_hours"
        case tokenGCDays = "token_gc_days"
    }
}

public struct RotaShift: Codable, Sendable {
    public let shiftID: String
    public let startsAtUTC: String
    public let endsAtUTC: String
    public let startsAtLocal: String
    public let endsAtLocal: String
    public let breakMinutes: Int
    public let paidHours: Double
    public let skillKey: String
    public let status: String

    enum CodingKeys: String, CodingKey {
        case shiftID = "shift_id"
        case startsAtUTC = "starts_at_utc"
        case endsAtUTC = "ends_at_utc"
        case startsAtLocal = "starts_at_local"
        case endsAtLocal = "ends_at_local"
        case breakMinutes = "break_minutes"
        case paidHours = "paid_hours"
        case skillKey = "skill_key"
        case status
    }
}

public struct MyShiftsResponse: Codable, Sendable {
    public let userID: String
    public let shifts: [RotaShift]
    public let totalPaidHours: Double

    enum CodingKeys: String, CodingKey {
        case userID = "user_id"
        case shifts
        case totalPaidHours = "total_paid_hours"
    }
}

/// The API's error envelope: `{"detail": {"code", "message", "detail"}}`.
struct ApiErrorEnvelope: Decodable, Sendable {
    struct Body: Decodable, Sendable {
        let code: String?
        let message: String?
    }

    let detail: Body?
}
