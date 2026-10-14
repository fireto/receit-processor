#!/usr/bin/env bash
# Pull the latest code and rebuild — run on the docker host in /opt/lab/receit-processor
# as worker.
set -euo pipefail
cd "$(dirname "$0")"
# git pull may rewrite this very file while bash is still reading it (bash reads scripts
# incrementally), so pull first and then re-exec the fresh copy exactly once.
if [ -z "${RECEIPTS_REBUILD_PULLED:-}" ]; then
    git pull --ff-only
    RECEIPTS_REBUILD_PULLED=1 exec "./$(basename "$0")" "$@"
fi
# The container runs as uid 1000; create data/ as us before docker creates it as root.
mkdir -p data
if [ "$(stat -c %u data)" != 1000 ]; then
    echo "data/ must be owned by uid 1000: sudo chown -R 1000:1000 data" >&2
    exit 1
fi
# `up -d --build` recreates only containers whose image or config changed;
# --remove-orphans drops the PoC's old `receipt-processor` service container.
docker compose up -d --build --remove-orphans
