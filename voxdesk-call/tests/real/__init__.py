"""Opt-in real external provider integration suite (`tests/real/`).

Every test in this package requires `VOXDESK_REAL_INTEGRATION=1` plus the
specific upstream provider credentials (`TWILIO_*`, `DEEPGRAM_*`, `OPENAI_*`,
`ANTHROPIC_*`, `ELEVENLABS_*`, `CARTESIA_*`, `GOOGLE_API_KEY`, `TELNYX_*`,
`SALESFORCE_*`, `VOXDESK_REAL_HUBSPOT_TOKEN`, `VOXDESK_REAL_WEBHOOK_RECEIVER_URL`).
Missing credentials never fake a pass: the test writes a structured
`SKIPPED_NO_CREDENTIALS` entry with `provider_fakes: false` via `evidence_writer`
and skips with the exact missing variable names.
"""
