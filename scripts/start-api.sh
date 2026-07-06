#!/bin/sh
# PaaS boot (Render/Railway/Fly): apply pending migrations, then serve.
# Single-token entrypoint so hosts whose start-command parsing mangles quoted
# `sh -c "..."` strings (e.g. Render's dockerCommand) can just run `start-api`.
# `aeo serve` reads $PORT via its typer envvar; host must be 0.0.0.0 in a container.
set -e
aeo migrate
exec aeo serve --host 0.0.0.0
