package com.voxdesk.sdk

/**
 * Device and push-token lifecycle.
 *
 * The three rules this class encodes, each of which is a real support ticket when a
 * host app gets it wrong:
 *
 * * `register` is idempotent and safe to call on every launch; the server scopes a
 *   row by (tenant, user, platform, device id) and clears a previous revocation,
 *   which is exactly what a reinstall is.
 * * The token goes up once with [uploadToken] and is never read back — the API only
 *   ever returns a six-character hint.
 * * [revoke] on sign-out. A device left registered keeps receiving the next person's
 *   rota notifications on a shared handset.
 */
class DevicesApi(private val client: VoxDeskClient) {

    suspend fun list(): List<Device> =
        client.get("/api/mobile/devices", DeviceListResponse.serializer()).devices

    suspend fun register(request: RegisterDeviceRequest): RegisterDeviceResponse =
        client.post(
            "/api/mobile/devices",
            request,
            RegisterDeviceRequest.serializer(),
            RegisterDeviceResponse.serializer(),
        )

    /** Upload (or replace) the FCM registration token for this device. */
    suspend fun uploadToken(deviceId: String, token: String) {
        client.postForget(
            "/api/mobile/devices/$deviceId/token",
            TokenRequest(token),
            TokenRequest.serializer(),
        )
    }

    /** Set the window, or pass `null` to clear it. */
    suspend fun setQuietHours(deviceId: String, quietHours: QuietHours?): Device =
        client.post(
            "/api/mobile/devices/$deviceId/quiet-hours",
            QuietHoursRequest(quietHours),
            QuietHoursRequest.serializer(),
            Device.serializer(),
        )

    /**
     * Revoke this device. A 404 means it is already gone, which is the same end
     * state, so it is not an error.
     */
    suspend fun revoke(deviceId: String) {
        try {
            client.delete("/api/mobile/devices/$deviceId")
        } catch (api: VoxDeskApiException) {
            if (api.status == 404) return
            throw api
        }
    }

    companion object {
        const val PLATFORM_ANDROID: String = "android"
        const val PLATFORM_IOS: String = "ios"
    }
}
