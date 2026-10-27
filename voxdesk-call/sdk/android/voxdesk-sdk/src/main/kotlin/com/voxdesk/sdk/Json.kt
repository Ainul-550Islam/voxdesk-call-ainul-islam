package com.voxdesk.sdk

import kotlinx.serialization.json.Json

/**
 * The one JSON configuration the SDK uses.
 *
 * `ignoreUnknownKeys` so a server that adds a field does not break an older app;
 * `explicitNulls = false` so `quiet_hours: null` is sent as an explicit null (which
 * the API reads as "clear the window") rather than being omitted (which it reads as
 * "leave it alone"). Those two are easy to confuse and the difference is a user's
 * notifications.
 */
internal object Wire {
    val json: Json = Json {
        ignoreUnknownKeys = true
        explicitNulls = false
        encodeDefaults = true
    }
}
