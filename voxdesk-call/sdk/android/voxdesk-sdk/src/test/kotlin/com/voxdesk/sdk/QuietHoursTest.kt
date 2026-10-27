package com.voxdesk.sdk

import java.time.Instant
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Quiet hours are the one piece of SDK logic that runs without a server, so it is
 * the one piece that is unit-tested here in full. Every case below is a real
 * question a support engineer has asked: "why did my phone stay silent at 10am?"
 */
class QuietHoursTest {

    private fun window(start: String, end: String, zone: String = "Asia/Dhaka", enabled: Boolean = true) =
        QuietHours(start = start, end = end, timezone = zone, enabled = enabled)

    @Test
    fun `span wraps through midnight`() {
        assertEquals(9 * 60, QuietHoursRules.spanMinutes("22:00", "07:00"))
        assertEquals(8 * 60, QuietHoursRules.spanMinutes("09:00", "17:00"))
        assertEquals(0, QuietHoursRules.spanMinutes("09:00", "09:00"))
    }

    @Test
    fun `accepts a window that crosses midnight`() {
        assertEquals(QuietHoursRules.Result.Ok, QuietHoursRules.validate(window("22:00", "07:00")))
    }

    @Test
    fun `rejects a zero length window`() {
        val result = QuietHoursRules.validate(window("09:00", "09:00"))
        assertTrue(result is QuietHoursRules.Result.Invalid)
        assertEquals(
            "a quiet window must have a non-zero length",
            (result as QuietHoursRules.Result.Invalid).reason,
        )
    }

    @Test
    fun `rejects a window longer than the ceiling`() {
        val result = QuietHoursRules.validate(window("06:00", "20:00"))
        assertEquals(
            "a quiet window may be at most 12 hours",
            (result as QuietHoursRules.Result.Invalid).reason,
        )
    }

    @Test
    fun `honours a deployment specific ceiling`() {
        assertEquals(
            QuietHoursRules.Result.Ok,
            QuietHoursRules.validate(window("06:00", "20:00"), maxSpanHours = 14),
        )
    }

    @Test
    fun `rejects malformed times and a blank zone`() {
        assertTrue(QuietHoursRules.validate(window("9am", "07:00")) is QuietHoursRules.Result.Invalid)
        assertTrue(QuietHoursRules.validate(window("22:00", "07:00", zone = "")) is QuietHoursRules.Result.Invalid)
    }

    @Test
    fun `is quiet inside a wrapping window using the device zone`() {
        // Dhaka is UTC+6 with no DST: 20:00Z is 02:00 local.
        assertTrue(QuietHoursRules.isQuietNow(window("22:00", "07:00"), Instant.parse("2026-10-26T20:00:00Z")))
        // 17:30Z is 23:30 local.
        assertTrue(QuietHoursRules.isQuietNow(window("22:00", "07:00"), Instant.parse("2026-10-26T17:30:00Z")))
        // 04:30Z is 10:30 local.
        assertFalse(QuietHoursRules.isQuietNow(window("22:00", "07:00"), Instant.parse("2026-10-26T04:30:00Z")))
    }

    @Test
    fun `the end of a window is already open`() {
        // 01:00Z is exactly 07:00 in Dhaka.
        assertFalse(QuietHoursRules.isQuietNow(window("22:00", "07:00"), Instant.parse("2026-10-26T01:00:00Z")))
    }

    @Test
    fun `a disabled or malformed window never silences notifications`() {
        assertFalse(
            QuietHoursRules.isQuietNow(
                window("22:00", "07:00", enabled = false),
                Instant.parse("2026-10-26T17:30:00Z"),
            )
        )
        assertFalse(
            QuietHoursRules.isQuietNow(window("nope", "07:00"), Instant.parse("2026-10-26T17:30:00Z"))
        )
        assertFalse(QuietHoursRules.isQuietNow(null, Instant.parse("2026-10-26T17:30:00Z")))
    }

    @Test
    fun `an unknown timezone is open rather than silent`() {
        assertFalse(
            QuietHoursRules.isQuietNow(
                window("22:00", "07:00", zone = "Mars/Olympus"),
                Instant.parse("2026-10-26T17:30:00Z"),
            )
        )
    }
}
