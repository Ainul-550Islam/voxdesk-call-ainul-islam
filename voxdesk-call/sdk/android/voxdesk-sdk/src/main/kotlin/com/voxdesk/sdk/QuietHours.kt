package com.voxdesk.sdk

import java.time.Instant
import java.time.ZoneId
import java.time.ZonedDateTime

/**
 * Quiet hours, validated locally and evaluated on the device's own clock.
 *
 * The server is the authority — `PUSH_QUIET_HOURS_MAX_SPAN` is a deployment setting,
 * and the API rejects a window it will not honour. This object exists so a settings
 * screen can say "that's 14 hours; the maximum is 12" without a round trip, and so a
 * local notification can decide correctly when the server is not in the loop.
 */
object QuietHoursRules {

    /** Same default ceiling the API uses; a caller may pass the server's value. */
    const val DEFAULT_MAX_SPAN_HOURS: Int = 12

    private val TIME = Regex("^([01]\\d|2[0-3]):([0-5]\\d)$")

    sealed class Result {
        object Ok : Result()
        data class Invalid(val reason: String) : Result()
    }

    fun validate(quietHours: QuietHours, maxSpanHours: Int = DEFAULT_MAX_SPAN_HOURS): Result {
        if (!TIME.matches(quietHours.start) || !TIME.matches(quietHours.end)) {
            return Result.Invalid("times must be HH:MM")
        }
        if (quietHours.timezone.isBlank()) {
            return Result.Invalid("a timezone is required")
        }
        val span = spanMinutes(quietHours.start, quietHours.end)
        if (span == 0) {
            return Result.Invalid("a quiet window must have a non-zero length")
        }
        if (span > maxSpanHours * 60) {
            return Result.Invalid("a quiet window may be at most $maxSpanHours hours")
        }
        return Result.Ok
    }

    /** Length in minutes, wrapping through midnight (22:00 to 07:00 is 540). */
    fun spanMinutes(start: String, end: String): Int {
        val startMinutes = toMinutes(start) ?: return 0
        val endMinutes = toMinutes(end) ?: return 0
        return ((endMinutes - startMinutes) + 24 * 60) % (24 * 60)
    }

    /**
     * Is [instant] inside the window, read in [timezone]?
     *
     * A malformed window or an unknown zone returns `false` on purpose: a broken
     * window must never silence a person's notifications. The end of a window is
     * exclusive, so 07:00 is already open.
     */
    fun isQuietNow(
        quietHours: QuietHours?,
        instant: Instant = Instant.now(),
        timezone: String = quietHours?.timezone ?: "UTC",
    ): Boolean {
        if (quietHours == null || !quietHours.enabled) return false
        if (!TIME.matches(quietHours.start) || !TIME.matches(quietHours.end)) return false
        val local: ZonedDateTime = try {
            instant.atZone(ZoneId.of(timezone))
        } catch (invalid: Exception) {
            return false
        }
        val now = local.hour * 60 + local.minute
        val start = toMinutes(quietHours.start) ?: return false
        val end = toMinutes(quietHours.end) ?: return false
        if (start == end) return false
        return if (start < end) now in start until end else now >= start || now < end
    }

    fun toMinutes(value: String): Int? {
        val match = TIME.find(value) ?: return null
        val hour = match.groupValues[1].toInt()
        val minute = match.groupValues[2].toInt()
        return hour * 60 + minute
    }
}
