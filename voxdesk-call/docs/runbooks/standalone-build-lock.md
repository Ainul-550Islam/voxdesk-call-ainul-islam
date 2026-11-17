# Runbook — the standalone build is locked or half-deployed

## Symptom A — the build refuses to start

```
A previous build that didn't exit cleanly. Run `rm -rf .next` and try again.
Suggestion: Wait for the build to complete.
```

The `.next` directory has a stale lock because an earlier build was killed —
usually by the shell timeout, not by an error.

## Check first

```sh
cd dashboard-next
pgrep -af "next build"      # is one still running?
ls -la .next/BUILD_ID       # a fresh timestamp means it actually finished
```

**If a build is still running, wait for it.** Deleting `.next` underneath a live
build produces exactly the corrupted artefact described in Symptom B. Watch it
with the process tooling rather than polling by hand; a full build here takes
38–43 s when the box is idle, and much longer when it is not (one took 1,750 s
under memory pressure while the API and Chromium were both resident).

## Symptom B — the standalone server 500s after a rebuild

```
Invariant: The client reference manifest for route "/dashboard" does not exist.
Error: Failed to load static file for page: /500 ENOENT .../pages/500.html
```

The `.next/standalone` tree is from one build and `.next/static` from another.
Deploying is a **three-step** operation and skipping the middle one produces
this:

```sh
rm -rf .next                 # only if a build was killed
npm run build
cp -r .next/static .next/standalone/dashboard-next/.next/
# public/ is optional — this repo has none, so a failing cp here is fine
cat .next/BUILD_ID .next/standalone/dashboard-next/.next/BUILD_ID   # must match
```

Then restart the server:

```sh
cd .next/standalone/dashboard-next
PORT=3100 HOSTNAME=0.0.0.0 node server.js
```

**Restarting is mandatory.** `node server.js` reads the manifests at boot; the
old process keeps serving the old build with the new build's chunk hashes and
every page 404s its own JavaScript.

## The free-a-port step

`EADDRINUSE: address already in use 0.0.0.0:3100` means the previous server is
still alive. The reliable way to find it:

```sh
ss -ltnp | grep ':3100'          # gives you the pid
kill -9 <pid> && sleep 3
ss -ltnp | grep ':3100' || echo "port free"
```

`pkill -f "node server.js"` sometimes misses it because the process renames
itself to `next-server (v16.4.3)`; match on the port, not the command line.

## Verify a deploy before running suites

```sh
curl -s -o /dev/null -w 'web=%{http_code}\n' http://127.0.0.1:3100/dashboard
curl -s http://127.0.0.1:8080/ | grep -o 'next_f[^<]\{0,40\}' | head -1
```

`web=200` from the standalone server and a matching build id in the HTML stream
mean the deploy is real. A `200` on `/` alone proves nothing — the marketing
pages are statically rendered and survive a mismatched `.next` long enough to
look healthy.

## Write down

- Whether the build was killed by a timeout or failed on its own.
- The two `BUILD_ID` values before and after the copy step.
