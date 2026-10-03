# ============================================================ stage 1: web ===
# CANONICAL PRODUCTION FRONTEND: dashboard/ (Vite + React)
# This is the sole frontend shipped in production per P0-01 audit finding.
# dashboard-next/ (Next.js) is a roadmap/shadow-parity migration target per
# docs/EXPANSION-ROADMAP.md Phase 1 and dashboard-next/README.md.
# It is CI-tested in .github/workflows/polyglot.yml but NOT included in this
# production image until full parity is proven. See docs/CURRENT-ARCHITECTURE.md
# and docs/DASHBOARD.md for the canonical topology.
# Build the React dashboard into static assets. This keeps node entirely out
# of the runtime image.
FROM node:20-alpine AS dashboard-build

WORKDIR /srv
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY dashboard/ ./
RUN npm run build

# ============================================================ stage 2: api ===
# Base images are pinned to a Debian codename so the OS userland does not
# float across major releases; the patch level within a codename still floats
# by design (security patches come in automatically). Moving this pin is a
# deliberate, tested change, never a blind upgrade — see docs/DEPLOYMENT.md.
FROM python:3.12-slim-bookworm

# ffmpeg: audio processing for the voice pipeline. libsndfile1 + build deps:
# several audio wheels (resampling, etc.) need them at install time.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application, migrations, worker scripts, and the built dashboard. Alembic is
# the sole schema owner in production, so the full alembic/ tree ships here and
# the entrypoint runs `alembic upgrade head` before serving.
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini .
COPY scripts ./scripts
COPY --from=dashboard-build /srv/dist ./dashboard/dist

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
