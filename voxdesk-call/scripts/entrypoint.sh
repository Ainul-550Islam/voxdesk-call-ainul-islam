#!/bin/sh
# Production entrypoint: migrate (under an advisory lock), then serve.
#
# Contract with Compose and `docker run`:
#   1. `python scripts/migrate.py` always runs first, so Alembic — the sole
#      schema owner in production and staging — is at head before any traffic.
#   2. If a command was supplied it is executed verbatim as PID 1. That is what
#      a Compose `command:` override reaches; dropping it silently replaced the
#      scheduler with a second uvicorn.
#   3. With no command, the production server defaults below apply.
#
# Alembic is the sole schema owner in production and staging. The application
# never runs Base.metadata.create_all there, so the schema MUST be brought up
# to date here before any traffic arrives.
#
# scripts/migrate.py wraps `alembic upgrade head` in a PostgreSQL advisory
# lock so that concurrent container starts serialize instead of racing DDL
# (see that file for the rationale). The lock is PostgreSQL-only, matching
# the production/staging topology.
set -e

python scripts/migrate.py

# A Compose service that sets `command:` supplies this container's real process
# (the scheduler runs `python -m scripts.scheduler`; the API may pin
# `--workers`). Migration always happens first and exactly once, then the
# caller's command owns the PID. Without this branch the entrypoint executed
# uvicorn unconditionally, so the scheduler service silently served HTTP with
# the API's default worker count instead of running its loop.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# uvicorn production defaults:
#   --workers            : one per CPU core (override with WEB_CONCURRENCY)
#   --proxy-headers      : trust X-Forwarded-Proto/Host from the TLS terminator
#                          so PUBLIC_BASE_URL, wss:// and Twilio signatures work
#   --forwarded-allow-ips: restrict which proxies are trusted. Production Compose
#                          sets this to the fixed Caddy bridge address; the
#                          local default trusts only loopback peers.
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --workers "${WEB_CONCURRENCY:-2}" \
  --proxy-headers \
  --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-127.0.0.1,::1}"
