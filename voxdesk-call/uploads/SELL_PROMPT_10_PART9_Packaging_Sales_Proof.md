# SELL PROMPT 10 of 10 — PART 9: Packaging and Sales Proof

Copy the whole block below with one click and paste it into the coding agent. It contains everything for this part: mission, rules, the file tree with a `# comment` beside every file, and the acceptance commands.

```text
# ======================================================================================================================
# SELL PROMPT 10 of 10 — PART 9: PACKAGING AND SALES PROOF   (Gate G9)
# Series: VoxDesk x Retell sell-ready gap closure (target sale band $30K-$60K) | repo root: voxdesk-call/ | master plan:
#   VOXDESK_SELL_READY_GAP_CLOSURE_30K_60K.md
# PREREQUISITE: All previous prompts accepted and scripts/fake_success_allowlist.txt is empty.
# MISSION: Package the evidence: due-diligence pack, verified feature matrix, SBOM and licence documents, quick-start
#   verified on a clean runner, sales documents generated from verified data only, release workflow and `make
#   verify-sale`.
# SCOPE GUARD: change only what appears in the tree below. Other prompts own the rest. Do not start the next prompt in
#   this session.
# RULES (apply to every file below)
#   R1  Read every file fully before editing. Output the COMPLETE final content of every created/modified file.
#       Never write "...", "rest unchanged", "omitted for brevity".
#   R2  Extend REAL assets (tag [KEEP]); never build a parallel copy of something that already works.
#   R3  NO FAKE SUCCESS: do the real external effect or return NOT_CONFIGURED / UNSUPPORTED_CAPABILITY /
#       PENDING_PROVIDER (HTTP 501/409). Never record delivered/connected/success without proof.
#   R4  NO filler, padding, clones or line-count targets: no StructN, *_function_N, /endpoint-N, "Padding ... line
#       N" or "1050+ lines" banners.
#   R5  Durable multi-worker state only (Postgres / Redis / app/jobs / app/outbox). No process-local dict/list for
#       idempotency, rate limits, OAuth state, sessions, queues.
#   R6  Every outbound URL goes through app/core/ssrf.py. Secrets via app/security/secret_store.py or
#       app/integrations/crm/crypto.py (AES-GCM). No base64/reverse "encryption", no fallback secrets.
#   R7  Every query tenant-scoped (and environment-scoped where modelled). Cross-tenant = 404. Each new route ships
#       a two-tenant isolation test.
#   R8  Audit/outbox writes happen in the same transaction as the action. No `except Exception: pass` around
#       audit/outbox/webhook code.
#   R9  Every task ships unit + contract tests. External calls get a recording-fake test (asserts the real request)
#       AND an opt-in @pytest.mark.real_provider test (existing marker; runs only with VOXDESK_REAL_INTEGRATION=1). No
#       passing test = not LIVE.
#   R9b New tests/<dir>/ gets __init__.py; reuse the existing real_provider marker (never add a second `live`
#       marker); register only slow/docker in pytest.ini.
#   R10 Alembic: exactly ONE head (as shipped: 0045_request_idempotency_receipts). Use the next free revision, never
#       edit an applied migration, run `alembic heads` before and after.
#   R11 Docs contain only measured facts (scripts/repo_stats.py). Anything without an end-to-end/contract test is
#       documented as API_ONLY or PLANNED.
#   R12 One commit per task ID: "<TASK-ID>: <what>". No generated bulk. No null-byte files.
#   R13 After this part write reports/PART_<n>_REPORT.md: files changed (complete content), real command output,
#       test counts pass/fail/skip, residual gaps. Never claim "production ready".
#   R14 pipecat-ai is pinned at 0.0.94: inspect the installed package and use only classes that exist there.
#   R15 Register every new router/WebSocket in app/main.py, add an RBAC permission + isolation test, regenerate
#       contracts (make contracts-check). Never reuse an existing file name (ls app/api first).
# TAGS: [NEW] create | [MODIFY] read fully, change only what is described, keep other behaviour | [DELETE] remove +
#   every import/registration/compose/CI reference | [KEEP] REAL asset, extend only as stated | [VERIFY] inspect first,
#   record the decision in the report
# COMMENT FORMAT: # [TAG][sub-part] kind — what the file contains (classes / functions / behaviour / tests). Several
#   changes to one file are merged on one line as "sub-part: change || sub-part: change".
# ======================================================================================================================

voxdesk-call/
├── .github/
│   └── workflows/
│       ├── real-integrations.yml      # [MODIFY] ci — add a nightly `schedule` trigger next to the existing manual
│       │                              #   `workflow_dispatch` (13 provider secrets behind a GitHub environment); tests marked
│       │                              #   `real_provider` run with VOXDESK_REAL_INTEGRATION=1 (Twilio sandbox call, Deepgram,
│       │                              #   ElevenLabs, LLM, Salesforce sandbox); results copied to evidence/
│       └── release.yml                # [NEW] ci — on tag: build images, run verify-sale, attach due-diligence zip + SBOMs
├── docs/
│   ├── api/                           # [NEW] dir (generated) — static API reference built from the OpenAPI schema
│   │                                  #   (Redoc/Scalar) by scripts/build_api_docs.sh
│   ├── DEMO/
│   │   └── SCRIPT.md                  # [NEW] doc — 10-minute demo script + the list of REAL recordings to capture (inbound
│   │                                  #   call, outbound batch, web call, live takeover, flow call, webhook delivery,
│   │                                  #   post-call analysis, dashboard)
│   ├── DUE_DILIGENCE/
│   │   ├── ARCHITECTURE.md            # [NEW] doc — mermaid diagrams: live call path, control plane, jobs/outbox, realtime
│   │   │                              #   gateway/SFU, web-call path
│   │   ├── GIT_HISTORY.md             # [NEW] doc — factual note on repository history (5 initial commits, the null-byte
│   │   │                              #   incident and its fix); no history rewriting
│   │   ├── KNOWN_LIMITATIONS.md       # [NEW] doc — honest list of API_ONLY / PLANNED / NOT_CONFIGURED items (generated
│   │   │                              #   from feature_manifest.yaml)
│   │   ├── README.md                  # [NEW] doc — index of evidence: architecture, test report, SBOM, license scan,
│   │   │                              #   security scan, load test, latency benchmark, demo recordings, repo stats, known
│   │   │                              #   limitations
│   │   └── TEST_REPORT.md             # [NEW] doc (generated) — real pytest/vitest/go/cargo counts, coverage, flaky list
│   ├── SALES/
│   │   ├── COMPETITIVE_COMPARISON.md  # [NEW] doc — vs Retell: only dated, sourced facts; vendor claims labelled as vendor
│   │   │                              #   claims
│   │   ├── LISTING_COPY.md            # [NEW] doc (generated) — Fiverr/Upwork copy built from FEATURE_MATRIX_VERIFIED.md
│   │   │                              #   (LIVE features only)
│   │   └── PRICING_AND_TIERS.md       # [NEW] doc — Band A/B/C contents mapped to the SELL GATES AND BANDS listed in this
│   │                                  #   block, support/handoff terms, exclusions; no unverified claims
│   ├── OPS.md                         # [MODIFY] doc — routine operations, upgrades, backups, rotations
│   └── QUICKSTART.md                  # [NEW] doc — clean-VM install in <= N commands (`make up`), seed demo tenant,
│                                      #   provider key checklist (Twilio, Deepgram, ElevenLabs, LLM), first test call;
│                                      #   verified by CI job on a clean runner
├── sbom/                              # [NEW] dir (generated) — CycloneDX SBOMs for Python, Node, Go, Rust
├── scripts/
│   ├── build_api_docs.sh              # [NEW] script — builds docs/api from the current OpenAPI snapshot
│   └── make_due_diligence_pack.py     # [NEW] script — bundles reports + evidence into dist/due-diligence-<date>.zip with
│                                      #   SHA256SUMS
├── CHANGELOG.md                       # [NEW] doc — tagged releases starting at the first truthful release
├── LICENSE                            # [NEW] doc — license/terms the buyer receives (owner decides; counsel review)
├── Makefile                           # [MODIFY] config — `verify-sale` = lint + typecheck + backend tests + frontend tests
│                                      #   + go/cargo tests + truth guards + contracts + SBOM + feature matrix + due-diligence
│                                      #   pack
├── NOTICE                             # [NEW] doc — attribution
├── SECURITY.md                        # [NEW] doc — vulnerability reporting, supported versions
└── THIRD_PARTY_LICENSES.md            # [NEW] doc (generated) — dependency licenses incl. assets/ambient samples

# ======================================================================================================================
# SELL GATES AND BANDS (the price range is the owner's; gates only define which claims are truthful)
# ======================================================================================================================
# G0 Truth: No filler, clones, facades, padding, fake success; guards in CI | closed by PART 0 | evidence: `make
#   verify-truth` green; `reports/PART_0_REPORT.md`
# G1 Evented platform: Call lifecycle webhooks really delivered; automatic post-call analysis; QA auto-review; retention
#   + PII enforced | closed by PART 1A, 1C, 1D, 1E | evidence: delivery logs from a real receiver; analysis rows for real
#   calls
# G2 Outbound: Real batch/power dialer: DNC, windows, pacing, retries, voicemail, no double-dial | closed by PART 1B |
#   evidence: campaign run against Twilio test numbers; concurrency test
# G3 Runtime proof: Measured latency report; smart turn-taking; provider failover; DTMF/end-call/IVR/voicemail tools;
#   number→agent binding | closed by PART 2 | evidence: `docs/LATENCY_BENCHMARK.md` from ≥200 real calls; tool tests
# G4 Web: Browser call + embeddable widget + web SDK working end to end | closed by PART 3 | evidence: recorded browser
#   call; SDK tests
# G5 Live ops: Real live listen / whisper-to-AI / takeover | closed by PART 4 | evidence: recorded takeover demo; authz
#   tests
# G6 Flow builder: Visual builder drives real calls | closed by PART 5 | evidence: e2e call following a published flow
# G7 Quality loop: LLM-caller simulations, regression-from-call, auto-QA, A/B with real routing, custom dashboards |
#   closed by PART 1G, 6 | evidence: experiment on live traffic; dashboards screenshots
# G8 Integrations: Salesforce real + ≥2 other CRMs verified live + automation connectors + Node/Web/Python SDKs | closed
#   by PART 1F, 3, 6 | evidence: `real_provider` test logs; published SDK packages
# G9 Enterprise & scale: SSO/SCIM verified against an IdP, capacity model, DR drill, due-diligence pack | closed by PART
#   7, 8, 9 | evidence: reports in `docs/DUE_DILIGENCE/`
# BAND A (≈ $30K): requires G0 + G1 + G2 + G3 | Verified multi-tenant voice-agent platform: telephony, outbound,
#   webhooks, post-call analysis, measured latency
# BAND B (≈ $45K): requires Band A + G4 + G5 + G7 | + browser calls/widget/SDK, live monitoring/takeover,
#   simulation/QA/A-B loop
# BAND C (≈ $60K): requires Band B + G6 + G8 + G9 | + visual flow builder, real CRM suite, enterprise identity, capacity
#   & due-diligence evidence

# ======================================================================================================================
# FINAL DEFINITION OF DONE
# ======================================================================================================================
# 1. `make verify-sale` exits 0 on a clean checkout and writes `dist/due-diligence-<date>.zip`.
# 2. `docs/SALES/FEATURE_MATRIX_VERIFIED.md` lists every feature with status and evidence; no LIVE feature without a
#   passing evidence test.
# 3. `scripts/fake_success_allowlist.txt` is empty.
# 4. `python -c "from app.main import app; …"` shows no `/endpoint-N` routes; `scripts/verify_no_filler.py` reports zero
#   findings.
# 5. Recordings exist for each LIVE headline feature; latency and capacity docs contain real data.
# 6. Every claim in listing copy and comparison docs maps to a matrix row.
# Do-not-claim list until its gate is green: web calling · live monitoring/takeover · visual flow builder · Salesforce ·
#   A/B testing on live traffic · automatic post-call analysis · ≈600 ms (or any) latency figure · SOC 2/HIPAA/ISO
#   attestation · multi-carrier AI media (Telnyx/SIP) · speech-to-speech.

# ======================================================================================================================
# ACCEPTANCE — run every command; paste the REAL output into reports/PART_9_REPORT.md
# ======================================================================================================================
make verify-truth                                   # truth guards must stay green
make verify-sale                                    # exits 0 on a clean checkout
ls dist/due-diligence-*.zip                          # the due-diligence pack exists
python scripts/generate_feature_matrix.py            # no LIVE feature without a passing evidence test
test ! -s scripts/fake_success_allowlist.txt          # allowlist is empty


# SERIES COMPLETE: run `make verify-sale`, then hand over dist/due-diligence-<date>.zip.
```
