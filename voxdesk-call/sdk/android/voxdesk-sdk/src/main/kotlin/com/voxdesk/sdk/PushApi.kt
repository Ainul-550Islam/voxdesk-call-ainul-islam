package com.voxdesk.sdk

/**
 * Push status and self-test.
 *
 * [test] exists so "notifications aren't arriving" is two taps instead of an
 * investigation: it pushes to the caller's own devices and returns the per-device
 * outcome, including the provider's error code.
 */
class PushApi(private val client: VoxDeskClient) {

    suspend fun deliveries(limit: Int? = null, status: String? = null): DeliveryList {
        val query = mutableMapOf<String, String>()
        limit?.let { query["limit"] = it.toString() }
        status?.let { query["status"] = it }
        return client.get("/api/mobile/deliveries", DeliveryList.serializer(), query)
    }

    /** Provider capability — booleans only, never credentials. */
    suspend fun providers(): ProviderStatus =
        client.get("/api/mobile/providers", ProviderStatus.serializer())

    /**
     * Send a test notification to this user's own devices. `urgent` bypasses quiet
     * hours and defaults to `false`, so a test cannot wake somebody up by accident.
     */
    suspend fun test(
        title: String = "",
        body: String = "",
        urgent: Boolean = false,
    ): PushTestResult =
        client.post(
            "/api/mobile/push/test",
            PushTestRequest(template = "push_test", title = title, body = body, urgent = urgent),
            PushTestRequest.serializer(),
            PushTestResult.serializer(),
        )
}
