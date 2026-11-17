#!/usr/bin/env bash
# Replay one ledger corpus entry into a wallet-backend binary and report a
# pass / mishandled / dropped verdict.
#
#   run_wallet_backend.sh LABEL BINARY ENTRY_DIR OUT_DIR
#
# The verdict comes from wb_verdict.py, which derives every expectation from the
# entry. Set STELLAR_XDR to the stellar-xdr CLI (default: the one on PATH) so
# signer keys can be read from the entry's ledger meta.
#
# Needs: a PostgreSQL with TimescaleDB reachable at $PGURL_BASE (no database
# name), python3, and `docker exec $DB_CONTAINER psql` for the checks.
# No network access and no keys: the ledger comes from mock_rpc.py, the
# history archive URL points at an empty local directory, and the ingest cursor
# is seeded to the ledger before the entry so no checkpoint is loaded.
set -euo pipefail

LABEL=$1 BIN=$2 ENTRY=$3 OUT=$4
HERE=$(cd "$(dirname "$0")" && pwd)
PGURL_BASE=${PGURL_BASE:-postgres://postgres@127.0.0.1:55432}
DB_CONTAINER=${DB_CONTAINER:-xdr-repro-db}
RPC_PORT=${RPC_PORT:-18729}
INGEST_PORT=${INGEST_PORT:-18730}
WAIT=${WAIT:-90}
STELLAR_XDR=${STELLAR_XDR:-$(command -v stellar-xdr || true)}

DB="wb_${LABEL//-/_}"
SEQ=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['ledger']['sequence'])" "$ENTRY/entry.json")
PASS=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['ledger']['networkPassphrase'])" "$ENTRY/entry.json")
mkdir -p "$OUT/empty-archive"
psql() { docker exec -i "$DB_CONTAINER" psql -U postgres -v ON_ERROR_STOP=1 -At "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB" >/dev/null
psql -d "$DB" -c "CREATE EXTENSION IF NOT EXISTS timescaledb" >/dev/null

export DATABASE_URL="$PGURL_BASE/$DB?sslmode=disable"
export RPC_URL="http://127.0.0.1:$RPC_PORT"
export NETWORK=testnet NETWORK_PASSPHRASE="$PASS" STELLAR_ENVIRONMENT=development
export LEDGER_BACKEND_TYPE=rpc GET_LEDGERS_LIMIT=10 INGEST_SERVER_PORT=$INGEST_PORT
export ARCHIVE_URL="file://$OUT/empty-archive" LOG_LEVEL=INFO

"$BIN" migrate up >"$OUT/$LABEL.migrate.log" 2>&1
psql -d "$DB" -c "INSERT INTO ingest_store (key, value) VALUES ('latest_ingest_ledger', '$((SEQ - 1))'), ('oldest_ingest_ledger', '$((SEQ - 1))') ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value" >/dev/null

python3 "$HERE/mock_rpc.py" --port "$RPC_PORT" "$ENTRY" >"$OUT/$LABEL.rpc.log" 2>&1 &
RPC_PID=$!
trap 'kill $RPC_PID 2>/dev/null || true' EXIT
sleep 1

"$BIN" ingest >"$OUT/$LABEL.ingest.log" 2>&1 &
ING_PID=$!
EXIT_CODE=running
for _ in $(seq "$WAIT"); do
  if ! kill -0 $ING_PID 2>/dev/null; then
    wait $ING_PID && EXIT_CODE=0 || EXIT_CODE=$?
    break
  fi
  grep -q "Ingested ledger $SEQ " "$OUT/$LABEL.ingest.log" && break
  sleep 1
done
if [ "$EXIT_CODE" = running ]; then
  kill $ING_PID 2>/dev/null || true
  wait $ING_PID 2>/dev/null || true
fi

# Verdict: wb_verdict.py compares the database with the entry's expect block.
python3 "$HERE/wb_verdict.py" --entry "$ENTRY" --db "$DB" --container "$DB_CONTAINER" \
  --label "$LABEL" --ingest-exit "$EXIT_CODE" ${STELLAR_XDR:+--stellar-xdr "$STELLAR_XDR"} | tee "$OUT/$LABEL.result.json"
