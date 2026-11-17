# ===================================================== stage 1: web-build =====
# CANONICAL PRODUCTION FRONTEND: dashboard-next/ (Next.js, `output: 'standalone'`).
#
# PART 17A (P17A-03) replaced the Vite build stage with this one. The Vite
# dashboard is no longer built *or* shipped: `dashboard/` was deleted once
# `docs/UI_PARITY.md` showed no PORT rows (P17A-05), and every page it served now
# lives in `dashboard-next/app` (49 marketing routes and 11 console pages ported
# verbatim in P17A-02, the rest already native).
#
# Why standalone and not a static export: the app has dynamic route segments
# (`/share/[token]`, `/use-cases/[slug]`, `/dashboard/agents/[id]/…`) whose values
# are leaf ids that do not exist at build time, and the CSP is nonce-based, which
# needs per-request HTML. `next.config.mjs` therefore emits `output: 'standalone'`
# and this stage produces a Node server, not a directory of files.
#
# The build context keeps the repository layout (`/srv/dashboard-next`,
# `/srv/sdk/web`) because `@voxdesk/web-sdk` is a `file:../sdk/web` dependency and
# because `outputFileTracingRoot` is the repository root — the traced standalone
# server is emitted at `.next/standalone/dashboard-next/`, which the runtime stage
# copies to `/app`.
FROM node:20-alpine AS web-build

WORKDIR /srv

# The workspace package is a build input, so it has to exist before `npm ci`
# resolves the `file:` dependency.
COPY sdk/web ./sdk/web
COPY dashboard-next/package.json dashboard-next/package-lock.json ./dashboard-next/

WORKDIR /srv/dashboard-next
RUN npm ci --no-audit --no-fund

COPY dashboard-next/ ./
RUN npm run build

# ============================================================ stage 2: web ====
# Runtime for the Next server. Node only: no Python, no shell tooling beyond the
# busybox userland, no package manager cache. The API image is separate and the
# two are joined by Caddy on the compose network.
FROM node:20-alpine AS web

WORKDIR /app

ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0 \
    NEXT_TELEMETRY_DISABLED=1

# `.next/standalone` is the traced server plus exactly the node_modules it uses.
# `.next/static` and `public/` are *not* included in it by design (they are
# content-addressed assets meant to be served by a CDN or reverse proxy), so they
# are copied alongside.
COPY --from=web-build /srv/dashboard-next/.next/standalone/dashboard-next ./
COPY --from=web-build /srv/dashboard-next/.next/static ./.next/static

# The base image ships an unprivileged `node` user (uid 1000) — no root runtime,
# same posture as the API image's `appuser`.
USER node

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]

# ==================================================== stage 2.5: quickjs ===
# Build QuickJS from the official source archive rather than copying a host
# binary: the API runtime is Debian Bookworm, and a binary from a newer host
# may require a newer glibc. The checksum pins the source release.
FROM python:3.12-slim-bookworm AS quickjs-build

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /tmp
RUN set -eux; \
    curl -fsSLo quickjs-2025-04-26.tar.xz \
      https://bellard.org/quickjs/quickjs-2025-04-26.tar.xz; \
    echo "2f20074c25166ef6f781f381c50d57b502cb85d470d639abccebbef7954c83bf  quickjs-2025-04-26.tar.xz" \
      | sha256sum -c -; \
    tar -xf quickjs-2025-04-26.tar.xz; \
    make -C quickjs-2025-04-26 -j"$(nproc)" qjs; \
    install -D -m 0755 quickjs-2025-04-26/qjs /out/qjs; \
    install -D -m 0644 quickjs-2025-04-26/LICENSE /out/LICENSE

# ============================================================ stage 3: api ===
# Base images are pinned to a Debian codename so the OS userland does not
# float across major releases; the patch level within a codename still floats
# by design (security patches come in automatically). Moving this pin is a
# deliberate, tested change, never a blind upgrade — see docs/DEPLOYMENT.md.
#
# This is the LAST stage on purpose: `docker build .` with no `--target` builds
# the API image, which is what CI and the existing tooling expect. The web image
# is built with `--target web` (docker-compose.prod.yml and full.yml do exactly
# that).
FROM python:3.12-slim-bookworm AS api

# ffmpeg: audio processing for the voice pipeline. libsndfile1 + build deps:
# several audio wheels (resampling, etc.) need them at install time.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential bubblewrap ffmpeg libsndfile1 util-linux \
    && rm -rf /var/lib/apt/lists/*

# The code-node runtime fails closed unless all three isolation tools exist.
# QuickJS was built above against the same Bookworm family as this image.
COPY --from=quickjs-build /out/qjs /usr/local/bin/qjs
COPY --from=quickjs-build /out/LICENSE /usr/share/licenses/quickjs/LICENSE
RUN command -v qjs >/dev/null \
 && command -v bwrap >/dev/null \
 && command -v prlimit >/dev/null

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application, migrations, worker scripts. Alembic is the sole schema owner in
# production, so the full alembic/ tree ships here and the entrypoint runs
# `alembic upgrade head` before serving.
#
# No frontend is copied into this image any more: the API serves JSON and
# WebSocket traffic, and Caddy routes documents to the `web` stage. That is what
# removes roughly a megabyte of HTML/JS from the API image and the reason
# `app/main.py`'s static mount is now a no-op in production (it still serves a
# Next export for a single-process layout; see `_mount_dashboard_if_built`).
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts

# Test configuration (FIX): pytest reads its options from pytest.ini — most
# importantly `asyncio_mode = auto`. Without this file in the image, pytest runs
# in its STRICT default and every plain `@pytest.fixture async def corpus(db, tenant_a, tenant_b):` fixture
# is handed to the test as an un-awaited coroutine instead of its value, which
# showed up as 35 spurious failures in tests/evals/rag plus further errors
# elsewhere. With the file present the same suite passes. Keeping it in the
# image means `docker compose exec api python -m pytest` behaves exactly like CI
# instead of silently testing something else.
COPY pytest.ini pyproject.toml ./

# Unprivileged runtime user; writable dirs for the local knowledge backend and
# mounted secrets.
RUN useradd --create-home --uid 10001 appuser \
 && mkdir -p /srv/var/knowledge /srv/secrets \
 && chown -R appuser:appuser /srv
USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200 else 1)"

# entrypoint applies migrations, then execs uvicorn with production defaults
# (multi-worker, proxy-header aware). See scripts/entrypoint.sh.
ENTRYPOINT ["sh", "scripts/entrypoint.sh"]
