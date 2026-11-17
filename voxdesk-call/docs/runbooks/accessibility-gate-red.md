# Runbook — the accessibility gate went red

## Before you read the violations

More than half of the failures this gate has produced were **not**
accessibility bugs. Check these three things first; each takes seconds and each
has produced a full page of red.

| Check | Command | What a bad answer means |
| --- | --- | --- |
| API alive | `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health` | `000` → the "violations" are the proxy's error page. See [api-oom.md](api-oom.md). |
| Page exists | `ls dashboard-next/app/dashboard/<route>` | A route on the list with no `page.tsx` audits a 404. The list is now derived; a hand-edited list is the bug. |
| Test timed out | look for `Test timeout of 120000ms exceeded` in `test-results/*/error-context.md` | The page polls and `networkidle` never fires. Not a violation. |

Real violations print like this in the error context:

```
Error: serious/critical accessibility violations on /dashboard/insight:
  label [critical] Form elements must have labels
      input[value="1000"]
  select-name [critical] Select element must have an accessible name
```

If you see a selector and an axe rule id, it is real. Fix it.

## Reading the failure

```sh
cd dashboard-next
find test-results -name error-context.md | while read f; do
  echo "== $f"; sed -n '/violations on/,/^expect(/p' "$f" | head -12
done
```

One page per error-context directory. The directory name carries the failing
page, so you can re-run a single page:

```sh
PLAYWRIGHT_BASE_URL=http://127.0.0.1:8080 E2E_EMAIL=… E2E_PASSWORD=… \
A11Y_CONSOLE_PAGES=/dashboard/insight \
  npx playwright test e2e/a11y.spec.ts -g "operator console"
```

## Fixing, by rule id

- **`label` / `select-name`** — give the control an `aria-label`, or an `id`
  with a matching `<label htmlFor>`. A `<label>` visually adjacent to the input
  is **not** a label; it has to be associated.
- **`link-in-text-block`** — the link must differ from surrounding text by more
  than colour: underline it. Watch out in `vite-port/**`: **Tailwind utilities
  are inert there** (no Tailwind build step), so `className="underline"` does
  nothing. Use an inline `style`, or add a `globals.css` rule.
- **`scrollable-region-focusable`** — anything with `overflow: auto` needs
  `tabIndex={0} role="region" aria-label="…"`. `\`.table-wrap\`` is already
  covered in 23 files; check whether you added a new one.
- **`dlitem`** — `<dt>`/`<dd>` must be inside a `<dl>`, not merely near one.
- **`document-title` / `html-has-lang`** — almost always a **render error**.
  Search that page for `#__next_error__` in the error context and treat it as a
  product bug: check the shape of the API response against what the page reads.
  This is exactly how `/dashboard/batch-calls` was found reading `batch_calls`
  from an endpoint that returns `batches`.

## Never

- Never widen the rule set or lower the severity to make a page pass.
- Never add a page to a skip list. If a page cannot be fixed now, fix the
  reason it cannot be audited, or leave it failing.
- Never fix `vite-port` styling with a Tailwind class. It will pass review and
  do nothing at runtime.

## Write down

- Which rule, which page, which element selector.
- Whether the root cause was the page, the API contract, or the harness — that
  distinction is what stops the next person re-debugging it.
