# VoxDesk Android SDK

Kotlin client for the VoxDesk mobile API: device registration, FCM token upload,
quiet hours, delivery history and the employee's own rota.

## Modules

| File | Responsibility |
| --- | --- |
| `VoxDeskClient.kt` | HTTP with auth, timeout, idempotency and typed errors. |
| `Models.kt` | Wire types; `@SerialName` maps camelCase properties to the API's snake_case. |
| `DevicesApi.kt` | Register/update/revoke this device, upload the FCM token. |
| `PushApi.kt` | Delivery history, self-test, provider capability. |
| `QuietHours.kt` | Local validation and the "is it quiet now" decision. |

## Build

```sh
./gradlew :voxdesk-sdk:assembleDebug   # library
./gradlew :voxdesk-sdk:test            # unit tests (JVM, no device needed)
```

The library targets Android 21+ and depends on `kotlinx.serialization` and
`kotlinx.coroutines` only — no Retrofit, no OkHttp. `HttpURLConnection` is enough for
a handful of JSON calls, and every dependency in a call-centre app has to be
justified.

## Rules the SDK enforces for you

* **Never send a `user_id`.** The server takes the person from the session.
* **Never log a push token or an access token.** `Log` calls contain identifiers only.
* **Treat 404 on revoke as success.** The device is gone either way.
* **Send `Idempotency-Key` on every mutation we retry.** `register()` retries on a
  transient failure; without the key that is two devices.
