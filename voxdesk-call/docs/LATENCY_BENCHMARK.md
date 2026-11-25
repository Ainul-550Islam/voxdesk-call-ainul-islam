# VoxDesk — Real PSTN & Voice-Pipeline Latency Evidence

This document reports only measurements derived from real external provider
traffic or real dual-channel carrier recordings (`provider_fakes: false`). Mock
pipeline timings and synthetic WAV results are isolated in
[`docs/LATENCY_BENCHMARK_MOCK.md`](LATENCY_BENCHMARK_MOCK.md). **No live PSTN
latency value is currently available; no value is inferred from a mock run.**

## Measurement method

`scripts/measure_pstn_latency.py` analyzes a dual-channel PCM16 WAV. By default,
channel 0 is caller audio and channel 1 is agent audio. It uses short-time 10 ms
RMS windows with a `-38.0 dBFS` threshold, at least 80 ms speech, and 150 ms
silence to detect caller end-of-speech. It then finds the first sustained
(20 ms) agent-audio onset and computes
`caller_leg_latency_ms = agent_first_audio_ms - caller_speech_end_ms`.
The output includes the measurement leg, provider, model, region, codec, sample
rate, channel indices, detected turns, and p50/p95/p99 where observations exist.
The supplied test callee plays a known short calibration motif. In the first 10 s,
normalized correlation must exceed 0.70 in **exactly one** channel; the analyzer
uses that as caller input and excludes the motif from all turn calculations. The
CSV retains the channel actually used. Without unambiguous calibration the
operator's requested map (default 0=caller, 1=agent) is provisional. For a
fully measured study the runner requires all requested calls, complete stage
coverage, successful cleanup and a calibrated recording for every completed
call. `COMPLETED_PROVISIONAL` preserves genuine partial metrics but exits nonzero.

The offline analysis is a signal-processing utility. Its synthetic stereo
fixture establishes that the detector returns a known injected offset; it does
not establish real PSTN, provider, media-stream, or model performance.

## N-call real PSTN study

`scripts/run_real_call_study.py` is the live-only runner. For each requested
call, it:

1. requires `VOXDESK_REAL_INTEGRATION=1`, a VoxDesk API key, Twilio credentials,
   provider test credentials, and an explicitly controlled destination;
2. creates the call through VoxDesk's authenticated `/api/calls` endpoint, so a
   durable `Call` row and signed outbound media URL exist;
3. waits for a completed Twilio call and a two-channel recording;
4. requires caller and assistant transcript turns through the authenticated
   replay endpoint, analyzes the recording, and writes per-call / aggregate
   caller-leg results to `real_study.json` and `real_study.csv`;
5. deletes the Twilio recording after analysis and does not retain raw WAV audio
   in the evidence bundle.

A supplied offline WAV has unknown carrier provenance. It can test the analyzer, not grant real-provider evidence. A carrier recording measures the call as recorded by the carrier, **not handset speaker/acoustic latency**. Calibrate channel order using recognizable, nonoverlapping scripted speech before publication; model/region/codec flags are operator labels, not discovered facts.

On the controlled staging deployment set `VOXDESK_STAGING_MEASUREMENTS=1`.
The runner joins the authenticated replay by **both** internal call ID and carrier
SID; its numeric `CallLatencyStat` fields are per-call medians. The stage p50/p95/p99
are percentiles **of the available call medians**, with coverage, not a pool of turns.
Missing stages remain null. Caller-leg percentiles use recording-derived individual turns.
An acoustic interruption means caller onset during agent speech; stop time is observed
on the agent channel. A clipped recording supplies no stop latency. Neither quantity
is a guarantee that an internal cancellation command succeeded. Recording cleanup
errors make the study non-green. Hardware fields describe the measurement client,
not necessarily the media worker. No live values are available in this checkout.

## Evidence included in this checkout

The [study record](../evidence/latency/2026-11-19/real_study.json), dated by the execution host, says `NOT_RUN_MISSING_CREDENTIALS`: **30 requested; 0 attempted; 0 completed**.
No turns were measured. The [CSV](../evidence/latency/2026-11-19/real_study.csv)
contains its header and no call rows. Missing percentiles are null, not 0 ms.

The separate [provider ledger](../evidence/real-integrations/2026-11-19/real_integrations.json)
for the same run date records **0 PASS; 15 SKIPPED_NO_CREDENTIALS**. Neither a
saved ledger nor `provider_fakes: false` establishes that a provider was reached.
There is no measured live caller-leg latency, stage TTFB, interruption time, or
carrier capacity result in this snapshot.

The automated proof that exists is limited to offline behavior: the unit test
`tests/truth/test_part11_evidence_and_bench.py::test_measure_pstn_latency_dual_channel_caller_leg_onset`
checks a synthetic stereo fixture with a known 420 ms offset. This is not a
product-performance result.

## Running an authorized study

The scripts default to one call, but this N-call runner places paid calls. Use only
a consented test callee/IVR under your control. Increase `--calls` only when the
additional spend and side effects are authorized. The remote VoxDesk deployment
must itself be configured with the required carrier/STT/LLM/TTS providers.

```bash
export VOXDESK_REAL_INTEGRATION=1
export VOXDESK_REAL_API_KEY="vxd_test_..."
export VOXDESK_PUBLIC_BASE_URL="https://voice-test.example.com"
export TWILIO_ACCOUNT_SID="AC..."
export TWILIO_AUTH_TOKEN="..."
export TWILIO_PHONE_NUMBER="+15550001111"
export TWILIO_TEST_TO_NUMBER="+15550002222"  # controlled, consented test callee
export DEEPGRAM_API_KEY="..."
export OPENAI_API_KEY="..."
export ELEVENLABS_API_KEY="..."

python scripts/run_real_call_study.py \
  --calls 1 \
  --date "$(date -u +%Y-%m-%d)"
```

The manual `real-integrations` workflow also accepts a `pstn_study_calls`
input (default `0`). A positive count explicitly requests that additional paid
study and uploads its JSON/CSV artifacts. The nightly schedule does not run the
extra N-call study. The [study runner](../scripts/run_real_call_study.py) documents
the output format and recording handling; [provider evidence status](EVIDENCE_STATUS.md)
links the dated integration ledger and its JUnit result.

## Controlled callee (operator-deployed only)

The repository includes `scripts/serve_study_callee.py`. On the owner’s staging host,
set `VOXDESK_STUDY_CALLEE=1`, `STUDY_CALLEE_PUBLIC_ORIGIN` (its public HTTPS origin),
the callee account’s `TWILIO_AUTH_TOKEN` and the approved `TWILIO_PHONE_NUMBER` / `TWILIO_TEST_TO_NUMBER`
pair. Serve `scripts.serve_study_callee:app` with Uvicorn behind that origin. Configure the
**dedicated, owned second number**, with permission, to POST to `/answer`. The responder verifies
Twilio’s signature and exact incoming number pair, plays a calibration motif and a short,
credited [spoken passage](../bench/fixtures/speech/README.md) three times with pauses, then hangs up.
It cannot dial, transfer or create recordings. The originating account must own or have verified
the destination. Run a one-call calibration and inspect the private recording before a larger study.

## Alerts

The voice-latency alerts live in `observability/alerts.yml` (mirrored into the
chart) and page on the SLOs in [`SLO-ALERTS.md`](SLO-ALERTS.md):

| Alert | Severity | Condition | Act |
|---|---|---|---|
| `VoiceLatencyP95Degraded` | warning | E2E turn p95 `> 1.2 s` for `10m` | [`RUNBOOKS/high-latency.md`](RUNBOOKS/high-latency.md) |
| `VoiceLatencyP95Critical` | critical | E2E turn p95 `> 2.0 s` for `2m` | [`RUNBOOKS/high-latency.md`](RUNBOOKS/high-latency.md) |

They are computed from `voxdesk_voice_e2e_latency_seconds_bucket`, which the
pipeline's `LatencyObserver` records per turn. **These alerts were dead links
until PART 17B**: both annotations pointed at `#alerts` in this file and this
section did not exist, so the page took the responder to the top of the page.
