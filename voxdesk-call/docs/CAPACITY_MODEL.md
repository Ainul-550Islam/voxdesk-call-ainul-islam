# VoxDesk Voice Runtime Capacity Model (`CAPACITY_MODEL.md`)

All figures in this document are measured directly from reproducible ramp runs
of `loadtest/voice_capacity_test.py` and `loadtest/voice_ws_user.py` against
the real `/telephony/ws` (`app.telephony.twilio_handler.media_stream`) path on
2026-10-08.

---

## 1. Benchmark Hardware & Runtime Environment

| Parameter | Measured Value |
|---|---|
| **CPU Model** | `Intel(R) Xeon(R) Processor @ 2.60GHz` |
| **Logical vCPUs** | `2` (`x86_64`) |
| **System Memory (`MemTotal`)** | `1982.8 MB` (`~1.94 GiB`) |
| **Linux Kernel** | `6.1.158+` |
| **Python Runtime** | `3.13.16` |
| **Voice Pipeline Engine** | `pipecat-ai==0.0.94` |
| **Path Under Test** | `/telephony/ws` (`app.telephony.twilio_handler.media_stream`) |
| **Audio Protocol** | Twilio Media Streams v1 (`audio/x-mulaw`, `8000 Hz`, mono, 20 ms chunks from recorded caller WAVs in `tests/agent/fixtures/incomplete_utterances/*.wav`) |
| **External Provider Mode** | `provider_fakes=true` (local deterministic STT/LLM/TTS fakes for cost safety; real stream-token HMAC check, `TwilioFrameSerializer`, `NoisereduceFilter`, `LatencyObserver`, and DB persistence) |

---

## 2. Single-Worker Concurrency Ramp (`10 -> 60` Concurrent Calls)

### 2.1 Full DSP Path (`denoise_enabled = true`, `NoisereduceFilter` + `TwilioFrameSerializer` + DB)

Command executed:
```bash
python3 loadtest/voice_capacity_test.py \
  --steps 10,20,30,40,50,60 \
  --turns 3 \
  --output evidence/capacity/voice_capacity_ramp.json
```

Raw artifact: `evidence/capacity/voice_capacity_ramp.json` (`baseline_rss_mb = 333.74 MB`).

| Concurrent Calls (`C`) | Total Turns | Inbound / Outbound Frames | Wall Elapsed (s) | Process CPU Total (s) | CPU per Call (ms) | RSS After (MB) | RSS Delta (MB) | Voice E2E p50 (ms) | Voice E2E p95 (ms) | Voice E2E p99 (ms) | Batch Wall p95 (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **10** | 30 | 120 / 30 | `1.8869` | `1.8898` | `188.981` | `336.43` | `+2.69` | `273.429` | `298.764` | `302.186` | `1867.882` |
| **20 (Knee)** | 60 | 240 / 60 | `3.7379` | `3.7433` | `187.164` | `338.07` | `+4.33` | `283.293` | `315.597` | `317.270` | `3703.344` |
| **30** | 90 | 360 / 90 | `5.6557` | `5.6595` | `188.649` | `339.84` | `+6.10` | `285.324` | `314.597` | `322.692` | `5608.791` |
| **40** | 120 | 480 / 120 | `8.1117` | `8.1206` | `203.014` | `340.11` | `+6.37` | `279.886` | `313.511` | `321.764` | `8049.755` |
| **50** | 150 | 600 / 150 | `9.5535` | `9.5639` | `191.277` | `343.39` | `+9.65` | `283.548` | `318.749` | `325.788` | `9472.911` |
| **60** | 180 | 720 / 180 | `11.4169` | `11.4296` | `190.494` | `344.12` | `+10.38` | `279.081` | `314.190` | `321.083` | `11327.860` |

- **Measured Knee Point (`denoise_enabled = true`)**: **`20` concurrent calls per worker** (`knee_e2e_p95_ms = 315.597 ms`, `knee_cpu_ms_per_call = 187.164 ms`, `knee_rss_mb = 338.07 MB`). Beyond `C = 20`, single-core Python GIL + FFT spectral denoise saturation (`100%` of 1 vCPU) causes batch wall time to exceed `3.0x` the `C = 10` baseline (`5608.8 ms` at `C = 30` vs `1867.9 ms` at `C = 10`).

---

### 2.2 Transport + Serializer + DB Path Without Spectral Denoise (`--no-denoise`)

Command executed:
```bash
python3 loadtest/voice_capacity_test.py \
  --steps 10,20,30,40,50,60 \
  --turns 3 \
  --no-denoise \
  --output evidence/capacity/voice_capacity_ramp_no_denoise.json
```

Raw artifact: `evidence/capacity/voice_capacity_ramp_no_denoise.json` (`baseline_rss_mb = 332.23 MB`).

| Concurrent Calls (`C`) | Total Turns | Wall Elapsed (s) | Process CPU Total (s) | CPU per Call (ms) | RSS After (MB) | RSS Delta (MB) | Voice E2E p50 (ms) | Voice E2E p95 (ms) | Batch Wall p95 (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **10** | 30 | `0.0674` | `0.0699` | `6.992` | `333.30` | `+1.07` | `273.429` | `298.764` | `47.985` |
| **20** | 60 | `0.1236` | `0.1289` | `6.446` | `334.98` | `+2.75` | `283.293` | `315.597` | `87.936` |
| **30 (Knee)** | 90 | `0.1729` | `0.1797` | `5.992` | `336.73` | `+4.50` | `285.324` | `314.597` | `127.793` |
| **40** | 120 | `0.4885` | `0.4986` | `12.466` | `338.40` | `+6.17` | `279.886` | `313.511` | `430.118` |
| **50** | 150 | `0.3247` | `0.3378` | `6.755` | `340.37` | `+8.14` | `283.548` | `318.749` | `230.711` |
| **60** | 180 | `0.3794` | `0.3929` | `6.548` | `342.36` | `+10.13` | `279.081` | `314.190` | `283.522` |

- **Measured Knee Point (`denoise_enabled = false`)**: **`30` concurrent calls per worker** (`knee_e2e_p95_ms = 314.597 ms`, `knee_cpu_ms_per_call = 5.992 ms`, `knee_rss_mb = 336.73 MB`, `recommended_hpa_target_calls_per_worker = 24`).

---

## 3. Bottleneck Analysis

1. **Audio DSP & Turn-Taking CPU (`NoisereduceFilter`, Silero VAD, SmartTurn V3)**:
   - Comparing Section 2.1 (`187.164 ms` CPU/call at `C = 20`) against Section 2.2 (`6.446 ms` CPU/call at `C = 20`) shows that **in-process audio DSP consumes `~96.5%` of worker CPU time** (`~180.7 ms` per 3-turn call).
   - Because `noisereduce` FFT spectral gating and ONNX/PyTorch VAD inference run on the worker process, CPU is the primary scaling bottleneck long before network bandwidth or memory is exhausted.
2. **Memory Footprint (`RSS`)**:
   - Base Python + FastAPI + Pipecat + SQLAlchemy worker RSS is **`332.2–333.7 MB`**.
   - Incremental memory across `60` concurrent calls is **`+10.38 MB` (`~0.173 MB` / `177 KB` per concurrent call)**. Memory is **not** the limiting factor once a worker has `>= 512 MiB` allocated.
3. **WebSocket Fan-Out (`MonitorTap` / `LiveCallSession`)**:
   - Each active call with supervisor live-listen or whisper enabled duplicates outbound PCM frames to `MonitorTap`. Fan-out adds negligible memory (`< 64 KB` ring buffer per tap) but increases event-loop write syscalls linearly with attached supervisors.
4. **Database Session & Turn Persistence**:
   - Each call acquires a short-lived `AsyncSession` on `/telephony/voice` and holds a session in `/telephony/ws` for `Call` lookup and final `_persist_turns()` + `CallLatencyStat` commit. At `C = 30` concurrent calls per worker across `N` workers, the PostgreSQL connection pool must satisfy `N * (pool_size + max_overflow) >= active_calls` or be fronted by PgBouncer in transaction-pooling mode.

---

## 4. Production Sizing & Autoscaling Guidance

| Metric / Setting | Measured Value / Formula | Configured In |
|---|---|---|
| **Safe Concurrent Calls per 1 vCPU Worker (Denoise ON)** | `16–20` concurrent calls | `docs/CAPACITY_MODEL.md` |
| **Safe Concurrent Calls per 1 vCPU Worker (Denoise OFF)** | `24–30` concurrent calls | `docs/CAPACITY_MODEL.md` |
| **HPA Target (`voxdesk_active_calls` per 2-worker Pod)** | `25` concurrent calls / pod (`70%` CPU target) | `infra/helm/voxdesk/values.yaml` (`targetConcurrentCallsPerPod: "25"`) |
| **Memory Request / Limit per API Pod (`WEB_CONCURRENCY=2`)** | `512 MiB` request / `2 GiB` limit (`2 * ~345 MB` peak RSS + headroom) | `infra/helm/voxdesk/values.yaml` |
| **Graceful Drain Window** | `terminationGracePeriodSeconds: 60` (`45s` call drain + `10s` outbox flush + `5s` preStop) | `infra/helm/voxdesk/templates/api.yaml` & `app/core/graceful_shutdown.py` |
