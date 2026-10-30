# Messaging Channels Architecture (`app/messaging/channels/`)

VoxDesk supports multi-carrier outbound and inbound messaging through the typed
`ChannelAdapter` registry (`app/messaging/channels/__init__.py`) mounted at `/api/channels`.

## Supported `(provider, channel_type)` Pairs

| Provider | Channel Type | Adapter Class | Outbound Endpoint | Webhook Signature Verification |
|---|---|---|---|---|
| `twilio` | `sms` | `TwilioSmsChannelAdapter` | `POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json` | `X-Twilio-Signature` HMAC-SHA1 (`twilio.request_validator.RequestValidator`) |
| `twilio` | `whatsapp` | `TwilioWhatsAppAdapter` | `POST https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json` (`whatsapp:+E164`, `ContentSid` templates) | `X-Twilio-Signature` HMAC-SHA1 |
| `telnyx` | `sms` | `TelnyxMessagingAdapter` | `POST https://api.telnyx.com/v2/messages` (Bearer token) | `telnyx-signature-ed25519` + `telnyx-timestamp` Ed25519 (`cryptography`) |
| `vonage` | `sms` | `VonageSmsAdapter` | `POST https://api.nexmo.com/v1/messages` | `Authorization: Bearer <HS256 JWT>` (`payload_hash` SHA-256) or `sig` HMAC-SHA256 |

Unsupported channel types (such as `slack`, `teams`, `email`, or `webchat`) are rejected
with **HTTP 422 (`UNSUPPORTED_CHANNEL`)**. Missing credentials fail closed with **HTTP 409
(`NOT_CONFIGURED`)** without creating fake delivery receipts.

## Required Environment Variables

- **Twilio SMS**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` (or per-channel `config.from_number`)
- **Twilio WhatsApp**: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_WHATSAPP_NUMBER`
- **Telnyx SMS**: `TELNYX_API_KEY`, `TELNYX_PUBLIC_KEY` (base64 or hex Ed25519 public key), `TELNYX_MESSAGING_PROFILE_ID` or `TELNYX_FROM_NUMBER`
- **Vonage SMS**: `VONAGE_API_KEY`, `VONAGE_API_SECRET`, `VONAGE_SIGNATURE_SECRET`, `VONAGE_FROM_NUMBER`

## Compliance Keyword Handling (TCPA / CTIA)

Inbound webhooks (`/api/channels/webhook/sms`, `/api/channels/webhook/whatsapp`,
`/api/channels/webhook/telnyx`, `/api/channels/webhook/vonage`) inspect single-word
messages before routing to an agent:

- **`STOP` / `STOPALL` / `UNSUBSCRIBE` / `CANCEL` / `END` / `QUIT`**: Adds the normalized E.164 sender to the tenant `dnc_entries` table (`app/telephony/dnc.py`) and returns an opt-out confirmation. Subsequent `POST /api/channels/send` calls to that number are blocked with HTTP `403` (`RECIPIENT_OPTED_OUT`).
- **`START` / `YES` / `UNSTOP`**: Removes the sender from `dnc_entries` and returns an opt-in confirmation.
- **`HELP` / `INFO`**: Returns standard opt-out/opt-in help text.

Every inbound webhook records a deduplicated `MessageWebhookReceipt` row keyed by `(channel, provider_message_id)` so carrier retries remain idempotent.
