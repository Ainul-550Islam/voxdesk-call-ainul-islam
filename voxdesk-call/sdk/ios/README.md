# VoxDesk iOS SDK

A Swift package (iOS 15+, macOS 12+) for the VoxDesk mobile API: device
registration, APNs token upload, quiet hours, delivery history and the employee's
own rota. No third-party dependencies — `URLSession` and `Codable` are enough, and
a call-centre app's dependency list gets audited.

## Files

| File | Responsibility |
| --- | --- |
| `VoxDeskClient.swift` | URL building, auth, timeout, `Idempotency-Key`, typed errors. |
| `Models.swift` | `Codable` wire types with `CodingKeys` for the API's snake_case. |
| `DevicesAPI.swift` | Register/update/revoke this device, upload the APNs token. |
| `PushAPI.swift` | Delivery history, self-test, provider capability. |
| `RotaAPI.swift` | `GET /api/wfm/my-shifts` — self-scoped, no user id parameter. |
| `QuietHours.swift` | Local validation and the "is it quiet now" decision. |

## Build and test

```sh
swift build          # macOS or on Linux with the open-source toolchain
swift test
```

In Xcode: **File → Add Package… → Add Local…** and select this directory.

## What the API guarantees, and why the SDK mirrors it

* **No `user_id` in any request.** The person comes from the session token.
* **A push token is write-only.** The API returns a six-character hint, never the
  token, so there is nothing to leak from local storage.
* **`DELETE /devices/{id}` is idempotent** and a 404 is success.
* **Mutations carry `Idempotency-Key`.** A retried registration is one device.

## Privacy manifest

`Sources/VoxDeskSDK/PrivacyInfo.xcprivacy` declares that the SDK stores nothing on
device beyond what the host app chooses to keep, so an App Store submission does
not trip over an undeclared data practice.
