#!/bin/sh
# PaaS boot (Render/Railway/Fly): apply pending migrations, then serve.
# Single-token entrypoint so hosts whose start-command parsing mangles quoted
# `sh -c "..."` strings (e.g. Render's dockerCommand) can just run `start-api`.
# `aeo serve` reads $PORT via its typer envvar; host must be 0.0.0.0 in a container.
set -e
aeo migrate
# Drain the Postgres job queue (agent runs) beside the API — single-container hosts
# (HF Space, Railway) have no separate worker service, and POST /api/agent/run only
# ENQUEUES: without a worker every run sits "queued" forever. The loop revives a
# crashed worker; `|| true` keeps a worker crash from ever taking the API down.
(while true; do aeo worker || true; sleep 5; done) &
exec aeo serve --host 0.0.0.0
