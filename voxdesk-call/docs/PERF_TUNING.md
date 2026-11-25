# Performance tuning — what was measured, not assumed

VoxDesk P17B-05. Every number here came from this repository's own build
running behind the front door at `:8080`, measured with Lighthouse 12 in the
desktop preset via `dashboard-next/lighthouserc.json`. Nothing in this file is a
vendor claim or an estimate.

Reproduce:

```sh
cd dashboard-next
CHROME_PATH=~/.cache/ms-playwright/chromium-1254/chrome-linux64/chrome \
  npx lhci autorun --config=lighthouserc.json
```

---

## 1. Current numbers

Standalone production build, `node server.js`, uvicorn on `:8000`, Caddy-style
front door on `:8080` (dev proxy in this sandbox).

| Page | Perf | A11y | Best practices | SEO | LCP | CLS | TBT |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `/` | 1.00 | 0.96 | 0.93 | 1.00 | 0.7 s | 0 | 10 ms |
| `/pricing` | 1.00 | 0.96 | 0.93 | 1.00 | 0.5 s | 0 | 20 ms |
| `/product` | 1.00 | 0.96 | 0.93 | 1.00 | 0.5 s | 0 | 0 ms |
| `/login` | 1.00 | 1.00 | 0.93 | 1.00 | 0.5 s | 0 | 10 ms |

These are localhost numbers with no network latency, so treat them as a
**regression floor**, not a forecast of field performance. What they are good
for: LCP above 1 s here means something is genuinely wrong, and CLS above 0.05
here means the layout itself is unstable.

---

## 2. The two regressions this file exists because of

Both were caught by the budget file on its first run. Neither was visible to
any functional or accessibility test.

### 2.1 Home page CLS 0.796

**Symptom.** Footer moved 1280×553 at y=167 to 1280×748 at y=7927, 329 ms after
navigation started.

**Cause.** `HomePage` renders a loading branch with a ~400 px skeleton while
`useHomeData()` resolves. That skeleton is short enough that the footer is
painted inside the first viewport. When the ten real sections mount, the footer
is pushed ~7,700 px down the document. Chrome charges every pixel of that move
to CLS, so the score is not "a small jump" — it is the entire document
reflowing.

**Fix.** Reserve the viewport for the loading body:

```tsx
<div className="mx-auto max-w-7xl px-4 py-20 sm:px-6 lg:px-8"
     style={{ minHeight: "calc(100vh - 4rem)" }}>
```

Now the footer is below the fold in both states, and its position is only
final once content is final.

**Measured after:** CLS 0.796 → 0.

### 2.2 Pricing page CLS 0.561

**Symptom.** A `SECTION` at 1280×328 collapsing to 0×0 at 219 ms.

**Cause.** The same shape at a smaller scale: a one-line
`Loading the server-side billing plan catalogue…` status row grows into
`VoiceAgentsPricing` + `PricingCalculator` + `PricingComparison` +
`PricingEnterprise` when `/api/v1/public/site/pricing` answers.

**Fix.** Reserve the viewport in the loading branch and mark it busy:

```tsx
<div role="status" aria-busy="true"
     style={{ minHeight: "calc(100vh - 18rem)" }}>
```

**Measured after:** CLS 0.561 → 0.

### 2.3 The cause that looked right and measured clean

`app/globals.css` line 7 was:

```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;…');
```

A cross-origin `@import` inside a stylesheet that the nonce CSP allows only
from `'self'`. It was blocked on every page load, and the console had been
logging the violation during accessibility runs:

```
Loading the stylesheet 'https://fonts.googleapis.com/…' violates the following
Content Security Policy directive: "style-src 'self' 'unsafe-inline'"
```

That is a textbook font-swap CLS. It was **not** the cause — CLS stayed at
0.796 after the import was removed. The shifts were structural (items below the
loading content being pushed down), not text metrics.

The import was replaced anyway, with a `@font-face` block whose `ascent-override`
/ `descent-override` / `size-adjust` describe the local fallback, because:

- a blocked stylesheet in the critical path is a wasted round trip on every
  page load, and
- it pollutes the console, which makes real CSP violations harder to see.

The same `@import` existed in `styles/enterprise.css` and was removed too.
`tests/frontend/test_frontend_gates.py::test_no_remote_stylesheet_import_remains`
now fails if one comes back.

---

## 3. What was deliberately not done

**No remote webfonts.** Loading Inter from a third party would mean widening
`style-src` and `font-src` to another origin, and giving a third party a request
log of every page view. The system stack renders the same glyphs; the design
tokens in `globals.css` are tuned for it. Adding a self-hosted `woff2` subset is
a reasonable future step, and it must be self-hosted — the CSP is the point.

**No code-splitting pass on the console.** The console is behind auth and its
users are operators with a warm cache. The marketing site, where a stranger's
first second matters, is already 1.00 performance.

**No image optimisation pass.** There are no raster images in the critical path.
The few marks are inline SVG.

**No third-party tag manager, analytics script, or chat widget.** Every one of
those is a render-blocking, CLS-generating, privacy-leaking default that has to
be justified individually. None is.

---

## 4. Levers, in the order they paid off here

1. **Reserve space for async content.** Two pages, two bugs, both fixed by
   `min-height`. This is the highest-value layout rule on this codebase.
2. **Do not wait for `networkidle` anywhere.** Five console pages poll; the
   event never fires. This cost three 120-second test timeouts that read as
   accessibility failures.
3. **Keep the critical path same-origin.** The `@import` fix above.
4. **Budget in CI, not in a spreadsheet.** `lighthouserc.json` made a 0.80 CLS
   fail a build. Without the budget, both regressions would have shipped and
   been discovered as a "the page jumps" bug report months later.

---

## 5. API-side numbers worth knowing

- **uvicorn reaches ~700–730 MB resident while browser suites are running, then
  falls back.** Measured: 727 MB immediately after a 56-page Playwright run,
  102 MB once the suites stopped, same process, no restart. The kernel log
  confirms three kills at `anon-rss:673808kB`, always during a full console
  audit. So the correct statement is not "this API needs 700 MB" — it is
  "this API touches 700 MB under concurrent load and does not release it
  quickly enough for a 2 GB box". Size pods for the peak, and track the peak.
- **The allocator flags were not the lever.** `MALLOC_ARENA_MAX=2` with
  `MALLOC_TRIM_THRESHOLD_`/`MALLOC_MMAP_THRESHOLD_` at 128 kB was set for the
  runs above and the peak was unchanged within noise. It neither helped nor
  hurt; it is not the reason the process now settles at 102 MB — the load
  ending is. Do not carry these flags forward expecting a fix.
- **2 GB of swap does remove the kill** on this sandbox. That is a mitigation
  for a small dev box, not a deployment recommendation: swapping under a
  memory-heavy process is slower, not faster, and the real answer is a bigger
  box or a smaller audit per process.

---

## 6. Per-call CPU on the voice pipeline preamble (PART 17B)

The voice path's per-call setup is pure Python and runs before any media moves:
`build_language_runtime`, `filter_enabled_tools`, `requires_runtime_stt`,
`_dynamic_variables_instruction`. It had never been measured, so "the pipeline is
slow, profile it" was an instruction with no baseline. This section is the
baseline, and the change that came out of it.

**Harness.** `scripts/bench_pipeline_preamble.py` — ships in the repo, runs the
exact four functions against a bilingual agent with an explicit allowlist and the
12 live tool schemas from `app/agent/functions.py`. Median of 5 rounds × 2000
calls, same machine, same interpreter, back to back on the two trees.

**Result.**

| Tree | median µs/call | min | max |
| --- | --- | --- | --- |
| before (PART 18 v6 tree, unmodified) | 39.19 | 31.07 | 45.99 |
| after (PART 17B) | **14.51** | 13.88 | 15.42 |
| repeat 1 | 45.20 → **16.65** | 39.21 → 15.68 | 48.40 → 22.61 |
| repeat 2 | 38.43 → **13.94** | 37.00 → 13.90 | 47.12 → 15.00 |

≈ **−63 %** of the preamble's CPU per call, consistently across runs (the two
trees' min/max ranges do not overlap). Reproduce:

```sh
python3 scripts/bench_pipeline_preamble.py          # 2000 calls × 5 rounds
python3 scripts/bench_pipeline_preamble.py --json   # same, machine-readable
```

**What was actually hot.** `cProfile` sorted by `tottime` put exactly two things
at the top, and neither was where the folklore pointed (no JSON encoding, no
prompt building, no provider client construction — those are noise at this size):

1. `filter_enabled_tools` — ~28 µs of the ~39 µs. Almost all of it was per-schema
   `str()`/`.strip()`/`.lower()` on values that were already strings, plus two
   dict lookups per schema. Rewritten to hoist the constant work, skip malformed
   entries structurally, and use `frozenset.isdisjoint` against the allowlist.
2. `build_language_policy` + `resolve_language_config` — called two to three times
   per call, each only reading the static locale tables in `app/core/i18n.py`.
   `resolve_language_config` is now `lru_cache`d (it returns a frozen dataclass, so
   sharing the instance cannot be mutated by a caller); `build_language_policy`
   memoizes its *fields* and still constructs a **fresh** `LanguagePolicy` per
   call, so no live call can observe another's state.

**What did not change — and how that is proven.** Behaviour is pinned by
`tests/agent/test_pipeline_cpu_budget.py` (12 tests: the full allowlist matrix,
alias direction, custom-tool survival, IVR removal, malformed schemas, the live
`TOOL_SCHEMAS` table, cache hit/miss behaviour, and that an invalid policy still
raises), plus the existing suites: `tests/agent` + `tests/telephony` → **1169
passed, 15 skipped**. A benchmark cannot fail; that test file can.

**Honest sizing.** 25 µs saved per call is not a latency win — a call lasts
minutes and the preamble is one of thousands of things that happen. At 10,000
calls an hour it is 0.25 s of CPU an hour. It is worth doing because it is free,
it is on the hot path, and it removes a repeated table lookup from every single
call — not because it will be felt. Anything claiming the pipeline got
"materially faster" from this would be overselling microsecond work, and the
numbers above are the whole claim.
