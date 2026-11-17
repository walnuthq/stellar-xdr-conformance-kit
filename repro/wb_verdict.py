#!/usr/bin/env python3
"""wallet-backend adapter: compare what an ingest run stored with what a ledger
corpus entry says a correct reader must produce.

  wb_verdict.py --entry ENTRY_DIR --db NAME [--container NAME] [--stellar-xdr PATH]
                [--label TEXT] [--ingest-exit CODE]

Everything expected comes from the entry: entry.json (ledger, focus,
expect.participants, expect.ledgerChanges) and, for signer keys, the entry's own
payload decoded with the stellar-xdr CLI. Nothing is passed by hand.

Checks, in order:
  ingested       the ingest cursor reached the entry's ledger   (else mishandled)
  focus-tx       the focus transaction was stored               (else dropped)
  participants   every expected participant is stored for the focus transaction
                 (else dropped); no stored participant key is malformed or is an
                 envelope-only address                          (else mishandled)
  ledger-change  per expect.ledgerChanges item this adapter can map:
                 trustline created -> a TRUSTLINE/ADD state change (else dropped)
                 account updated   -> for each signer the meta shows added, a
                                      SIGNER/ADD state change (else dropped) that
                                      holds the full signer key (else mishandled)
                 other entry types are reported as "not-mapped"
Overall verdict: mishandled if any check is, else dropped if any is, else pass.
"""
import argparse
import base64
import json
import pathlib
import subprocess
import sys

ACCOUNT, CONTRACT = 0x30, 0x10  # strkey version bytes G and C
TOID_OP_MASK = 0xFFF


def strkey_bytes(s):
    """Version byte + payload of a strkey, without the 2-byte checksum."""
    raw = base64.b32decode(s + "=" * (-len(s) % 8))
    return raw[:-2]


def psql(container, db, sql):
    out = subprocess.run(
        ["docker", "exec", "-i", container, "psql", "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1", "-At", "-F", "|", "-c", sql],
        capture_output=True, text=True, check=True,
    ).stdout
    return [line.split("|") for line in out.splitlines() if line]


def signers_added(entry, entry_dir, cli):
    """{account strkey: [signer strkey, ...]} added in the focus operation(s), read from the meta."""
    p = next(p for p in entry["payloads"] if p["role"] == "primary")
    js = subprocess.run(
        [cli, "decode", "--type", p["xdrType"], "--input", "single", "--output", "json"],
        input=(entry_dir / p["file"]).read_bytes(), capture_output=True, check=True,
    ).stdout
    lcm = json.loads(js)[f"v{entry['ledger']['lcmVersion']}"]
    meta = lcm["tx_processing"][entry["focus"]["txIndex"]]["tx_apply_processing"]
    ops = next(iter(meta.values()))["operations"]
    if "opIndex" in entry["focus"]:
        ops = [ops[entry["focus"]["opIndex"]]]
    added, before = {}, {}
    for op in ops:
        for change in op["changes"]:
            kind, body = next(iter(change.items()))
            acct = body.get("data", {}).get("account") if isinstance(body, dict) else None
            if not acct:
                continue
            keys = [s["key"] for s in acct.get("signers", [])]
            if kind == "state":
                before[acct["account_id"]] = keys
            elif kind in ("updated", "created"):
                new = [k for k in keys if k not in before.get(acct["account_id"], [])]
                if new:
                    added.setdefault(acct["account_id"], []).extend(new)
    return added


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", required=True)
    ap.add_argument("--db", required=True)
    ap.add_argument("--container", default="xdr-repro-db")
    ap.add_argument("--stellar-xdr")
    ap.add_argument("--label", default="")
    ap.add_argument("--ingest-exit", default="")
    a = ap.parse_args()
    entry_dir = pathlib.Path(a.entry)
    entry = json.loads((entry_dir / "entry.json").read_text())
    seq, focus, expect = entry["ledger"]["sequence"], entry.get("focus"), entry["expect"]
    q = lambda sql: psql(a.container, a.db, sql)
    checks = []

    def check(name, verdict, **detail):
        checks.append({"check": name, "verdict": verdict, **detail})

    cursor = int(q("SELECT value FROM ingest_store WHERE key = 'latest_ingest_ledger'")[0][0])
    check("ingested", "pass" if cursor == seq else "mishandled", cursorAfter=cursor, ledger=seq)

    if cursor == seq and focus:
        tx = q(f"SELECT to_id FROM transactions WHERE encode(hash, 'hex') = '{focus['txHash']}'")
        check("focus-tx", "pass" if tx else "dropped", txHash=focus["txHash"])
        if tx:
            to_id = int(tx[0][0])
            in_tx = f"BETWEEN {to_id} AND {to_id + TOID_OP_MASK}"

            if "participants" in expect:
                stored = {r[0] for r in q(
                    f"SELECT DISTINCT encode(account_id, 'hex') FROM (SELECT account_id FROM transactions_accounts WHERE tx_to_id = {to_id} "
                    f"UNION ALL SELECT account_id FROM operations_accounts WHERE operation_id {in_tx}) p")}
                want = {strkey_bytes(s).hex(): s for s in expect["participants"]["addresses"]}
                envelope_only = {strkey_bytes(s)[:33].hex()[2:]: s for s in expect["participants"].get("envelopeOnly", [])}
                missing = [s for k, s in want.items() if k not in stored]
                bad = []
                for k in sorted(stored - set(want)):
                    b = bytes.fromhex(k)
                    if len(b) != 33 or b[0] not in (ACCOUNT, CONTRACT):
                        why = "not an account or contract key"
                        if k[2:] in envelope_only:
                            why += f"; the first 32 key bytes of envelope-only {envelope_only[k[2:]]}"
                        bad.append({"key": k, "why": why})
                extra = sorted(k for k in stored - set(want) if k not in {x["key"] for x in bad})
                check("participants", "mishandled" if bad else "dropped" if missing else "pass",
                      expected=sorted(want), stored=sorted(stored), missing=missing, malformed=bad, extraValid=extra)

            changes = q(
                f"SELECT state_change_category, state_change_reason, encode(account_id, 'hex'), coalesce(encode(signer_account_id, 'hex'), '') "
                f"FROM state_changes WHERE to_id = {to_id}")
            added = None
            for lc in expect.get("ledgerChanges", []):
                name = f"ledger-change {lc['entryType']} {lc['change']}"
                if lc["entryType"] == "trustline" and lc["change"] == "created":
                    acct = lc["key"]["trustline"]["account_id"]
                    hit = [c for c in changes if c[0] == "TRUSTLINE" and c[1] == "ADD" and c[2] == strkey_bytes(acct).hex()]
                    check(name, "pass" if hit else "dropped", account=acct, expected="TRUSTLINE/ADD state change",
                          stored=sorted({f"{c[0]}/{c[1]}" for c in changes}))
                elif lc["entryType"] == "account" and lc["change"] == "updated":
                    acct = lc["key"]["account"]["account_id"]
                    if not a.stellar_xdr:
                        check(name, "skipped", why="needs the stellar-xdr CLI to read added signers from the meta")
                        continue
                    if added is None:
                        added = signers_added(entry, entry_dir, a.stellar_xdr)
                    if not added.get(acct):
                        check(name, "not-mapped", account=acct, why="the meta shows no signer added; other account changes are not mapped")
                        continue
                    rows = [c[3] for c in changes if c[0] == "SIGNER" and c[1] == "ADD" and c[2] == strkey_bytes(acct).hex()]
                    for signer in added[acct]:
                        want_key = strkey_bytes(signer).hex()
                        verdict = "pass" if want_key in rows else "mishandled" if rows else "dropped"
                        check(name, verdict, account=acct, signer=signer, expectedSignerKey=want_key,
                              expectedBytes=len(want_key) // 2, storedSignerKeys=rows, storedBytes=[len(r) // 2 for r in rows])
                else:
                    check(name, "not-mapped", why="this adapter has no wallet-backend row for this entry type")

    verdicts = [c["verdict"] for c in checks]
    overall = "mishandled" if "mishandled" in verdicts else "dropped" if "dropped" in verdicts else "pass"
    out = {"entry": entry["id"], "target": "stellar/wallet-backend", "label": a.label, "ledger": seq, "verdict": overall}
    if a.ingest_exit:
        out["ingestExit"] = a.ingest_exit
    out["checks"] = checks
    json.dump(out, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
