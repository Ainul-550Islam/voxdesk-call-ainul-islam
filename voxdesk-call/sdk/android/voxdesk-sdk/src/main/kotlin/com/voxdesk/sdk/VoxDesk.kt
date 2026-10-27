package com.voxdesk.sdk

/**
 * Entry point: one client, three APIs.
 *
 * ```kotlin
 * val voxdesk = VoxDesk(
 *     VoxDeskClient(baseUrl = "https://acme.voxdesk.example") {
 *         sessionStore.accessToken()
 *     }
 * )
 * voxdesk.devices.register(
 *     RegisterDeviceRequest(platform = DevicesApi.PLATFORM_ANDROID, deviceId = Settings.Secure.getString(cr, Settings.Secure.ANDROID_ID))
 * )
 * ```
 */
class VoxDesk(client: VoxDeskClient) {
    val client: VoxDeskClient = client
    val devices: DevicesApi = DevicesApi(client)
    val push: PushApi = PushApi(client)
    val rota: RotaApi = RotaApi(client)
}
