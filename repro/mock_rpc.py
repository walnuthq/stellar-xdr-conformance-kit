#!/usr/bin/env python3
"""Minimal offline Stellar RPC that serves the ledger entries of a corpus.

Answers the two JSON-RPC methods a ledger reader needs to pull ledgers:
getHealth and getLedgers. Every other method returns "method not found" and
is logged, so a run shows exactly what the reader asked for.

Usage: mock_rpc.py --port 8000 ENTRY_DIR [ENTRY_DIR ...]
Each ENTRY_DIR holds an entry.json of kind "ledger" and its primary payload.
Standard library only; binds to 127.0.0.1.
"""
import argparse
import base64
import hashlib
import json
import pathlib
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LEDGERS = {}  # sequence -> getLedgers LedgerInfo


def load(entry_dir):
    d = pathlib.Path(entry_dir)
    entry = json.loads((d / "entry.json").read_text())
    if entry["kind"] != "ledger":
        sys.exit(f"{d}: not a ledger entry")
    p = next(p for p in entry["payloads"] if p["role"] == "primary")
    raw = (d / p["file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != p["sha256"]:
        sys.exit(f"{d}: payload digest does not match the manifest")
    lg = entry["ledger"]
    LEDGERS[lg["sequence"]] = {
        "hash": lg["hash"],
        "sequence": lg["sequence"],
        "ledgerCloseTime": str(lg["closeTime"]),
        "headerXdr": "",
        "metadataXdr": base64.b64encode(raw).decode(),
    }


def bounds():
    lo, hi = min(LEDGERS), max(LEDGERS)
    return lo, hi, LEDGERS[lo]["ledgerCloseTime"], LEDGERS[hi]["ledgerCloseTime"]


def get_health(_params):
    lo, hi, lo_t, hi_t = bounds()
    return {
        "status": "healthy",
        "latestLedger": hi,
        "latestLedgerCloseTime": hi_t,
        "oldestLedger": lo,
        "oldestLedgerCloseTime": lo_t,
        "ledgerRetentionWindow": hi - lo + 1,
    }


def get_ledgers(params):
    lo, hi, lo_t, hi_t = bounds()
    start = int(params.get("startLedger", lo))
    limit = int((params.get("pagination") or {}).get("limit", 5))
    seqs = [s for s in sorted(LEDGERS) if s >= start][:limit]
    return {
        "ledgers": [LEDGERS[s] for s in seqs],
        "latestLedger": hi,
        "latestLedgerCloseTime": int(hi_t),
        "oldestLedger": lo,
        "oldestLedgerCloseTime": int(lo_t),
        "cursor": str(seqs[-1]) if seqs else "",
    }


METHODS = {"getHealth": get_health, "getLedgers": get_ledgers}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        method, params = req.get("method"), req.get("params") or {}
        out = {"jsonrpc": "2.0", "id": req.get("id")}
        if method in METHODS:
            out["result"] = METHODS[method](params)
        else:
            out["error"] = {"code": -32601, "message": "method not found"}
        print(f"rpc {method} {json.dumps(params, separators=(',', ':'))[:120]}", flush=True)
        body = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("entries", nargs="+")
    args = ap.parse_args()
    for e in args.entries:
        load(e)
    print(f"serving ledgers {sorted(LEDGERS)} on 127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
