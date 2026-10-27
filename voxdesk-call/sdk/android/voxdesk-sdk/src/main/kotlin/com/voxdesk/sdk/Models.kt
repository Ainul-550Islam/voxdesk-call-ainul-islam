package com.voxdesk.sdk

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/**
 * Wire models for `/api/mobile/*`.
 *
 * Every property is `@SerialName`-mapped to the API's snake_case name. The class
 * properties stay camelCase because Kotlin callers read them, but the mapping is
 * explicit: `ignoreUnknownKeys` lets the server add a field without breaking an
 * older app, and a *renamed* field fails loudly here instead of silently arriving
 * as a default.
 */
@Serializable
data class Device(
    val id: String,
    @SerialName("user_id") val userId: String,
    val platform: String,
    @SerialName("device_id") val deviceId: String,
    @SerialName("app_version") val appVersion: String = "",
    @SerialName("os_version") val osVersion: String = "",
    @SerialName("push_enabled") val pushEnabled: Boolean = true,
    @SerialName("quiet_hours") val quietHours: QuietHours? = null,
    @SerialName("last_seen_at") val lastSeenAt: String? = null,
    @SerialName("revoked_at") val revokedAt: String? = null,
    @SerialName("created_at") val createdAt: String? = null,
    val tokens: List<PushTokenSummary> = emptyList(),
)

@Serializable
data class QuietHours(
    val start: String,
    val end: String,
    val timezone: String,
    val enabled: Boolean = true,
)

@Serializable
data class PushTokenSummary(
    val id: String,
    val platform: String,
    val hint: String,
    val active: Boolean,
    @SerialName("failure_count") val failureCount: Int = 0,
    @SerialName("last_error") val lastError: String = "",
    @SerialName("invalidated_at") val invalidatedAt: String? = null,
)

@Serializable
data class DeviceListResponse(
    val devices: List<Device> = emptyList(),
    val count: Int = 0,
)

@Serializable
data class RegisterDeviceRequest(
    val platform: String,
    @SerialName("device_id") val deviceId: String,
    @SerialName("app_version") val appVersion: String = "",
    @SerialName("os_version") val osVersion: String = "",
    @SerialName("push_enabled") val pushEnabled: Boolean = true,
    @SerialName("quiet_hours") val quietHours: QuietHours? = null,
    val token: String? = null,
)

@Serializable
data class RegisterDeviceResponse(
    val id: String,
    @SerialName("user_id") val userId: String,
    val platform: String,
    @SerialName("device_id") val deviceId: String,
    val created: Boolean = false,
    @SerialName("quiet_hours") val quietHours: QuietHours? = null,
)

@Serializable
data class TokenRequest(val token: String)

@Serializable
data class QuietHoursRequest(@SerialName("quiet_hours") val quietHours: QuietHours?)

@Serializable
data class Delivery(
    val id: String,
    @SerialName("device_id") val deviceId: String = "",
    val template: String = "",
    val provider: String = "",
    val status: String = "",
    @SerialName("provider_message_id") val providerMessageId: String = "",
    @SerialName("error_code") val errorCode: String = "",
    @SerialName("error_reason") val errorReason: String = "",
    @SerialName("sent_at") val sentAt: String? = null,
)

@Serializable
data class DeliveryList(
    val deliveries: List<Delivery> = emptyList(),
    val summary: Map<String, Int> = emptyMap(),
)

@Serializable
data class PushTestRequest(
    val template: String = "push_test",
    val title: String = "",
    val body: String = "",
    val urgent: Boolean = false,
)

@Serializable
data class PushTestResult(
    val attempts: List<PushAttempt> = emptyList(),
    val sent: Int = 0,
    val failed: Int = 0,
    val suppressed: Int = 0,
)

@Serializable
data class PushAttempt(
    @SerialName("device_id") val deviceId: String = "",
    val platform: String = "",
    val status: String = "",
    @SerialName("dedupe_key") val dedupeKey: String = "",
    @SerialName("error_code") val errorCode: String = "",
    @SerialName("error_reason") val errorReason: String = "",
)

@Serializable
data class ProviderCapability(
    val enabled: Boolean = false,
    val configured: Boolean = false,
    val sandbox: Boolean = false,
)

@Serializable
data class ProviderStatus(
    val fcm: ProviderCapability = ProviderCapability(),
    val apns: ProviderCapability = ProviderCapability(),
    @SerialName("quiet_hours_max_span_hours") val quietHoursMaxSpanHours: Int = 12,
    @SerialName("token_gc_days") val tokenGcDays: Int = 60,
)

/** The API's error envelope: `{"detail": {"code", "message", "detail"}}`. */
@Serializable
data class ApiErrorEnvelope(val detail: ApiErrorBody? = null)

@Serializable
data class ApiErrorBody(
    val code: String = "error",
    val message: String = "",
    val detail: JsonElement? = null,
)
