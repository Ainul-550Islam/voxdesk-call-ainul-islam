package com.voxdesk.sdk

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * URL construction and wire mapping. These run on the JVM without a device or an
 * Android SDK, which is the point: the parts of the SDK that can be wrong in a
 * silent way (a doubled slash, a dropped null, a leaky field) are the parts that
 * are tested.
 */
class ClientTest {

    private val client = VoxDeskClient(baseUrl = "https://acme.example/")

    @Test
    fun `trailing slash in the base url never doubles up`() {
        assertEquals("https://acme.example/api/mobile/devices", client.url("/api/mobile/devices"))
        assertEquals("https://acme.example/api/mobile/devices", client.url("api/mobile/devices"))
    }

    @Test
    fun `query parameters are encoded and blanks dropped`() {
        assertEquals(
            "https://acme.example/api/mobile/deliveries?limit=20&status=failed",
            client.url("/api/mobile/deliveries", mapOf("limit" to "20", "status" to "failed", "q" to "")),
        )
    }

    @Test
    fun `a space in a query value is percent-encoded, not plus-encoded`() {
        assertEquals(
            "https://acme.example/api/wfm/my-shifts?start=2026-10-05T00%3A00%3A00Z",
            client.url("/api/wfm/my-shifts", mapOf("start" to "2026-10-05T00:00:00Z")),
        )
    }

    @Test
    fun `construction refuses a base url without a scheme`() {
        val failure = runCatching { VoxDeskClient("acme.example") }.exceptionOrNull()
        assertTrue(failure is IllegalArgumentException)
    }

    @Test
    fun `idempotency keys are unique per call`() {
        val keys = (1..50).map { VoxDeskClient.newIdempotencyKey() }.toSet()
        assertEquals(50, keys.size)
    }

    @Test
    fun `wire names follow the API, not Kotlin`() {
        val encoded = Wire.json.encodeToString(
            RegisterDeviceRequest.serializer(),
            RegisterDeviceRequest(
                platform = DevicesApi.PLATFORM_ANDROID,
                deviceId = "hardware-abc",
                appVersion = "1.0.0",
                osVersion = "34",
                token = "fcm-token",
            ),
        )
        assertTrue(encoded.contains("\"device_id\":\"hardware-abc\""))
        assertTrue(encoded.contains("\"app_version\":\"1.0.0\""))
        assertTrue(encoded.contains("\"os_version\":\"34\""))
        assertTrue(encoded.contains("\"push_enabled\":true"))
        assertTrue(!encoded.contains("user_id"))
    }

    @Test
    fun `clearing quiet hours sends an explicit null`() {
        val encoded = Wire.json.encodeToString(QuietHoursRequest.serializer(), QuietHoursRequest(null))
        assertEquals("{\"quiet_hours\":null}", encoded)
    }

    @Test
    fun `unknown response fields do not break an older app`() {
        val payload = """
            {"id":"dev-1","user_id":"u-1","platform":"android","device_id":"d-1",
             "quiet_hours":{"start":"22:00","end":"07:00","timezone":"Asia/Dhaka","enabled":true},
             "brand_new_field":"ignored"}
        """.trimIndent()
        val device = Wire.json.decodeFromString(Device.serializer(), payload)
        assertEquals("dev-1", device.id)
        assertEquals("22:00", device.quietHours?.start)
    }
}
