package com.voxdesk.sdk

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** One exported shift. `paidHours` is net of unpaid breaks. */
@Serializable
data class RotaShift(
    @SerialName("shift_id") val shiftId: String,
    @SerialName("user_id") val userId: String = "",
    @SerialName("starts_at_utc") val startsAtUtc: String,
    @SerialName("ends_at_utc") val endsAtUtc: String,
    @SerialName("starts_at_local") val startsAtLocal: String = "",
    @SerialName("ends_at_local") val endsAtLocal: String = "",
    @SerialName("break_minutes") val breakMinutes: Int = 0,
    @SerialName("paid_hours") val paidHours: Double = 0.0,
    @SerialName("skill_key") val skillKey: String = "",
    val status: String = "",
)

@Serializable
data class MyShiftsResponse(
    @SerialName("user_id") val userId: String = "",
    val shifts: List<RotaShift> = emptyList(),
    @SerialName("total_paid_hours") val totalPaidHours: Double = 0.0,
)

/**
 * The caller's own rota — the reason the app exists for most agents.
 *
 * There is no `userId` parameter anywhere in this class: the API takes the person
 * from the session, so an app cannot ask for a colleague's shifts even by accident.
 */
class RotaApi(private val client: VoxDeskClient) {

    suspend fun myShifts(start: String? = null, end: String? = null): MyShiftsResponse {
        val query = mutableMapOf<String, String>()
        start?.let { query["start"] = it }
        end?.let { query["end"] = it }
        return client.get("/api/wfm/my-shifts", MyShiftsResponse.serializer(), query)
    }
}
