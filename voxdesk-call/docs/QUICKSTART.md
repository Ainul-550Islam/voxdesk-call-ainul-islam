# VoxDesk Clean-VM Quickstart (<= 5 Commands)

This guide brings up a complete, migrated, seeded VoxDesk stack on a clean Ubuntu
22.04 / 24.04 or macOS host with Docker Engine and Docker Compose v2 installed,
and runs a verified synthetic voice call in **5 commands**.

---

## 1. Prerequisites

- **Docker Engine** `>= 24.0` and **Docker Compose** `>= 2.20`
- **Python** `>= 3.11` (optional on host if running CLI helpers outside Docker)
- **4 GB RAM**, **2 vCPU**, and **10 GB free disk**

---

## 2. Clean-VM Install & First Call in 5 Commands

```bash
# Command 1: Create your local .env with fresh cryptographic keys
cp .env.example .env && python3 -c '
import secrets
from pathlib import Path
p = Path(".env")
text = p.read_text() if p.exists() else ""
if "VOXDESK_ENCRYPTION_KEYS=" not in text:
    text += f"\nVOXDESK_ENCRYPTION_KEYS=k1:{secrets.token_hex(32)}\n"
p.write_text(text)
'

# Command 2: Boot PostgreSQL 16, Redis 7, FastAPI API, Scheduler, and run Alembic migrations
make up

# Command 3: Seed the demo tenant, owner account, and sample published agent version
make seed

# Command 4: Verify liveness and readiness probes (PostgreSQL @ 0062_drop_pcap_artifacts + Redis)
curl -fsS http://localhost:8000/health/ready

# Command 5: Place a deterministic synthetic voice call over /telephony/ws and print latency stats
docker compose exec -T api python loadtest/voice_ws_user.py --self-test
```

### Expected Output from Command 4 & Command 5

`GET http://localhost:8000/health/ready`:
```json
{
  "status": "ready",
  "shutting_down": false,
  "active_calls": 0,
  "checks": {
    "database": {"ok": true, "head_revision": "0062_drop_pcap_artifacts"},
    "redis": {"ok": true}
  }
}
```

`python loadtest/voice_ws_user.py --self-test`:
```json
{
  "call_id": "...",
  "turns": 3,
  "outbound_ws_messages": 6,
  "stt_p50_ms": 22.4,
  "llm_ttft_p50_ms": 38.1,
  "tts_ttfb_p50_ms": 26.8,
  "e2e_p50_ms": 285.3,
  "e2e_p95_ms": 318.7
}
```

---

## 3. Provider Key Checklist (For Live PSTN & Live AI Calls)

VoxDesk boots and passes all offline CI and synthetic call tests **without**
external provider keys. To place or receive live PSTN calls and use live cloud
STT/LLM/TTS providers, populate the following variables in `.env` (or configure
them per tenant via the encrypted SecretStore API `POST /api/secrets`):

| Provider | Purpose | Environment Variables in `.env` | How to Verify |
|---|---|---|---|
| **Twilio** | Inbound/outbound PSTN calls, Media Streams (`/telephony/ws`), SMS, AMD, Number Provisioning, Trust Hub | `TWILIO_ACCOUNT_SID=AC...`<br>`TWILIO_AUTH_TOKEN=...`<br>`TWILIO_WEBHOOK_BASE_URL=https://<your-public-domain>` | `pytest -m live tests/test_real_providers.py -k twilio` |
| **Deepgram** | Streaming Speech-to-Text (`nova-2` / `nova-3`, multilingual, keyword boosting) | `DEEPGRAM_API_KEY=...` | `pytest -m live tests/test_real_providers.py -k deepgram` |
| **ElevenLabs** | Streaming Text-to-Speech (`eleven_turbo_v2_5`, voice catalog, instant voice cloning) | `ELEVENLABS_API_KEY=...` | `pytest -m live tests/test_real_providers.py -k elevenlabs` |
| **OpenAI** | Primary LLM (`gpt-4o-mini` / `gpt-4o`), fallback STT (`whisper-1`), fallback TTS (`tts-1`), Post-Call Analysis & Auto-QA | `OPENAI_API_KEY=sk-...` | `pytest -m live tests/test_real_providers.py -k openai` |
| **Anthropic / Gemini** *(Optional)* | Secondary failover LLMs (`claude-3-5-sonnet`, `gemini-1.5-pro`) | `ANTHROPIC_API_KEY=...`<br>`GEMINI_API_KEY=...` | Automatic failover via `FailoverLLMService` |
| **Salesforce** *(Optional)* | Native OAuth 2.0 + PKCE CRM Contact/Lead upsert & Task/Case/Opportunity writeback | `SALESFORCE_CLIENT_ID=...`<br>`SALESFORCE_CLIENT_SECRET=...`<br>`SALESFORCE_REDIRECT_URI=https://<domain>/api/integrations/salesforce/callback` | `pytest tests/integrations/test_salesforce_provider.py` |

---

## 4. Placing Your First Live Inbound PSTN Call

1. Expose port `8000` over HTTPS (via your ingress controller, Cloudflare Tunnel, or `ngrok http 8000`).
2. Set `TWILIO_WEBHOOK_BASE_URL=https://<your-tunnel-domain>` in `.env` and restart the API (`docker compose up -d api`).
3. Bind your Twilio phone number to the seeded published agent version:
   ```bash
   curl -X PATCH http://localhost:8000/api/v1/telephony/numbers/<number_id> \
     -H "Authorization: Bearer <access_token>" \
     -H "Content-Type: application/json" \
     -d '{"inbound_agent_version_id": "<published_version_id>"}'
   ```
4. Dial your Twilio phone number from any phone. Twilio invokes `POST /telephony/voice`, upgrades to `wss://<your-tunnel-domain>/telephony/ws`, and streams bi-directional audio through the Pipecat pipeline.

---

## 5. Browser Web-Call Quick Test

Open the operator dashboard (`http://localhost:5173` in dev or `http://localhost:8000` when static-built) -> **Agents -> Test in Browser**, or embed the widget on any origin-allowlisted page:

```html
<script
  src="http://localhost:8000/widget/v1/embed.js"
  data-public-key="pk_live_..."
  data-agent-id="<agent_uuid>"
  async
></script>
```
