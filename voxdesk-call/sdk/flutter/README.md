# VoxDesk Dart / Flutter SDK

Thin, typed client over the VoxDesk REST API. Ships the same public surface as
the CLI (`sdk/cli`) and the Node SDK: agents, calls, the workflow library and
public share-link redemption.

```dart
final client = VoxDeskClient(apiKey: Platform.environment['VOXDESK_API_KEY']!);
final page = await client.listAgents();
```

## Error model

Every failure is a `VoxDeskException` with a `code`:

| Code | Meaning |
| --- | --- |
| `http_<status>` | the server answered that status with no structured detail |
| the server's code | e.g. `review_missing`, `passcode_required`, `budget_requests_exhausted` |
| `timeout` | the request exceeded `timeout` (default 20 s) |
| `network_error` | DNS/socket failure |

There is no automatic retry: `redeemShare` consumes one use of a link, and a
blind retry would spend two.

## Evidence tier

`CI_ONLY` — the package and its tests are complete and committed, but no Dart
toolchain is installed in this environment, so `dart test` has not been run
here. Run `cd sdk/flutter && dart pub get && dart test` on a machine with the
Flutter SDK; the suite is written against `package:http/testing.dart` and needs
no network.
