# `@voxdesk/mobile`

React Native client for the VoxDesk mobile API (`/api/mobile/*` and the
employee-scoped parts of `/api/wfm/*`).

## What it does

| Area | Call | Notes |
| --- | --- | --- |
| Device registration | `DeviceApi.register()` | Idempotent; call on every launch. Clears a previous revocation (reinstall). |
| Push token | `DeviceApi.registerToken()` | Written once, never read back. The server keeps a six-character hint only. |
| Quiet hours | `DeviceApi.setQuietHours()` | Validated locally and by the server; the server's ceiling wins. |
| Sign-out | `DeviceApi.revoke()` | Required: a device left registered keeps receiving someone else's notifications. |
| Delivery history | `PushApi.deliveries()` | Includes a per-status summary, so support can see *why* nothing arrived. |
| Self-test | `PushApi.test()` | Pushes to the caller's own devices only. |
| Providers | `PushApi.providers()` | Booleans only — never credentials. |
| Rota, time off, swaps | `WfmApi.*` | Self-scoped: the person comes from the session, never from the body. |

## Design rules

* **No token is ever stored by this library.** `getAccessToken` is a callback; the
  host app owns the keychain and the refresh flow.
* **Errors are typed.** `ApiError` carries the server's machine-readable `code`;
  `NetworkError` means no answer arrived. Branch on `code`, never on message text.
* **Mutating retries send an idempotency key.** `WfmApi.requestTimeOff` and
  `offerSwap` require one, because phones retry.
* **Nothing is inferred from silence.** A 204 is `undefined`, not `{}`.

## Install

```sh
npm install @voxdesk/mobile
```

React Native supplies `fetch`; if a host environment does not, pass `fetchImpl`.

## Scripts

```sh
npm run typecheck   # tsc --noEmit
npm run build       # tsc -> dist/
npm test            # vitest run
```
