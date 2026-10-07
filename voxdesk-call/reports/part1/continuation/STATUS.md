# PART 1 continuation — 1C provider boundary, not completed 1C

## Current authorization and scope

The user explicitly approved necessary supporting-file changes while preserving existing architecture and unrelated work. The user selected **finish remaining PART 1 now; PART 2 instructions later**. Continue in the specified order: 1C, 1D, 1E, 1B, 1F, 1G. No PART 2 implementation was started.

## Restored 1A Git state

At the start of this continuation, the persisted workspace had all delivered 1A files and artifacts, but Git HEAD was 1d023b5, not the previously reported 0a1a18c. All 36 delivered source files were checked byte-for-byte against the prior SHA-256 manifest before recreating the scoped 1A commit. Current restored commit: **2ed87d0**. The unrelated staged AB3 patch was compared before and after and remains byte-for-byte unchanged. This is a restored commit with a new hash, not a claim that the earlier commit was present.

## Actual new implementation

`app/ai/post_call_llm.py` provides summarize, classify_sentiment and extract_fields through the existing AI gateway. It adds real OpenAI, Anthropic and Google structured HTTP adapters using configured credentials, SSRF/DNS validation, redirects disabled, bounded response streaming and a hard deadline. The gateway continues to own routing, budget admission, provider fallback, circuit breaking and token attribution.

Strict JSON Schema validation rejects unknown/malformed fields when the supplied schema forbids them; remote schema references are rejected. Duplicate JSON keys, non-finite values and numeric overflow are rejected. Invalid JSON gets at most two measured invocations, without echoing rejected output into repair prompts. Missing provider configuration returns not_configured and no fabricated value. Unknown token usage and cost stay unknown rather than becoming zero. Every actual repair invocation has a distinct gateway usage key, so an invalid first answer is not silently unmetered.

The caller still owns the database transaction. This helper neither creates a second billing ledger nor claims durable completion before its caller commits. It does not yet constitute an end-to-end post-call pipeline.

## Actual verification

- Restored pinned production dependencies into a Python 3.12.15 venv. Inspected installed pipecat-ai **0.0.94** and verified OpenAILLMService, AnthropicLLMService and GoogleLLMService imports. The new one-shot HTTP adapter does not assume new Pipecat APIs.
- `OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python -m pytest tests/ai -q --junitxml=reports/part1/continuation/ai-regression.xml`: **95 passed, 1 skipped, 445 warnings, 42.00s**.
- `PATH=$PWD/.venv/bin:$PATH OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 make verify-truth`: **41 passed, 59 warnings, 19.76s**.
- `.venv/bin/ruff check app/ai/post_call_llm.py tests/ai/test_post_call_llm.py`: **All checks passed**.
- `PATH=$PWD/.venv/bin:$PATH make contracts-check`: **PASS**, five proto files compile with matching vocabulary and tenant scoping. The first run failed because protoc was absent in the restored environment; the original failure log is retained. Installed protobuf-compiler and reran the same command.
- One new fallback contract initially assumed the default fallback was Google. The real gateway selected Anthropic. The fixture was corrected to explicitly disable Anthropic using the actual persisted tenant policy, testing the intended Google fallback without changing routing or weakening assertions. The failure log is retained.

Recording-fake HTTP contracts assert provider destination, method, credentials, payload and actual response parsing for all three providers. Tests cover repair metering in durable UsageEvent rows, cross-tenant usage isolation, unknown cost, budget denial, DNS metadata rejection, redirect refusal, provider errors, hard timeouts, response-size bounds and real gateway fallback.

The single skipped test is the explicit live provider opt-in. It requires VOXDESK_POST_CALL_LIVE=1 and VOXDESK_POST_CALL_LIVE_KEY; VOXDESK_POST_CALL_LIVE_MODEL optionally selects the OpenAI model. No live provider credential was supplied or external LIVE pass claimed. This live test exercises the provider transport; it is not full post-call pipeline certification.

## Still required — do not mark 1C or PART 1 complete

- Durable POST_CALL job type, handler registration and terminal-transition enqueue.
- Per-step durable pipeline rows, real transcript finalization, summary/sentiment/outcome persistence, custom AnalysisResult persistence and schema-version uniqueness.
- Migration for the pipeline and existing analysis/backfill models.
- Analysis service and thin CRUD/run/backfill/status/results/export API with durable idempotency, rate limiting, audit and tenant/environment isolation.
- QA sampling/review enqueue, call_analyzed outbox publication, CRM/workflow integration and end-to-end worker tests.
- All remaining 1D, 1E, 1B, 1F and 1G work and acceptance.

The current post-call facade allowlist entry remains, because the facade is not closed. No 1C completion commit has been made. The new implementation/test files remain uncommitted until the complete task is verified. Only the previously completed 1A commit was restored.

No full-suite, clean-clone, deployment, hosted LIVE, production-ready or PART 1 completion claim is made. Provider URLs still have the documented DNS-rebinding window of the shared guard. This helper currently respects the existing gateway input bound rather than silently truncating long transcripts; pipeline chunking/long-call policy remains outstanding.
