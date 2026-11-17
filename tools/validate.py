#!/usr/bin/env python3
"""Check the corpus entries against the manifest schema.

For every corpus/entries/<id>/entry.json:
  1. validate against schema/manifest.schema.json (JSON Schema 2020-12);
  2. check the directory name, payload sizes and SHA-256 digests;
     check that the generator recipe exists and, for an XDR-JSON recipe,
     that it encodes to the payload;
  3. if a stellar-xdr CLI is available: decode each payload, re-encode it and
     compare bytes, then evaluate every expect.decoded pointer and the ledger
     facts against the decoded XDR-JSON.
Then mutate a valid entry in ways the schema must reject, and build the lines
of corpus/index.jsonl.

Usage: validate.py [--stellar-xdr PATH] [--write-index | --check-index]
       (needs: pip install jsonschema)
  --write-index   regenerate corpus/index.jsonl
  --check-index   fail if corpus/index.jsonl differs from the generated lines
Offline; reads only files in this repository.
"""
import argparse
import copy
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

from jsonschema import Draft202012Validator, FormatChecker

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRIES = ROOT / "corpus" / "entries"
INDEX = ROOT / "corpus" / "index.jsonl"
SCHEMA = json.loads((ROOT / "schema" / "manifest.schema.json").read_text())
Draft202012Validator.check_schema(SCHEMA)
ENTRY = Draft202012Validator(SCHEMA, format_checker=FormatChecker())
INDEX_LINE = Draft202012Validator(
    {"$schema": SCHEMA["$schema"], "$ref": "#/$defs/indexLine", "$defs": SCHEMA["$defs"]}
)


def resolve(doc, pointer):
    """RFC 6901 JSON Pointer."""
    if pointer == "":
        return doc
    for token in pointer.split("/")[1:]:
        token = token.replace("~1", "/").replace("~0", "~")
        doc = doc[int(token)] if isinstance(doc, list) else doc[token]
    return doc


def xdr(cli, *args, stdin=None):
    return subprocess.run([cli, *args], input=stdin, capture_output=True, check=True).stdout


def check_entry(path, cli):
    errors = []
    entry = json.loads(path.read_text())
    errors += [f"schema: {'/'.join(map(str, e.absolute_path))}: {e.message}" for e in ENTRY.iter_errors(entry)]
    if errors:
        return entry, errors
    if entry["id"] != path.parent.name:
        errors.append(f"id {entry['id']!r} does not match directory {path.parent.name!r}")
    decoded = {}
    for p in entry.get("payloads", []):
        raw = (path.parent / p["file"]).read_bytes()
        if len(raw) != p["bytes"]:
            errors.append(f"{p['name']}: {len(raw)} bytes, manifest says {p['bytes']}")
        if hashlib.sha256(raw).hexdigest() != p["sha256"]:
            errors.append(f"{p['name']}: sha256 mismatch")
        if cli:
            js = xdr(cli, "decode", "--type", p["xdrType"], "--input", "single", "--output", "json", stdin=raw)
            back = xdr(cli, "encode", "--type", p["xdrType"], "--input", "json", "--output", "single", stdin=js)
            if back != raw:
                errors.append(f"{p['name']}: does not round-trip byte-identical")
            decoded[p["name"]] = json.loads(js)
            if p["role"] == "primary":
                decoded[None] = decoded[p["name"]]
    gen = entry["provenance"].get("generator")
    if gen:
        recipe = ROOT / gen["path"]
        if not recipe.is_file():
            errors.append(f"generator.path {gen['path']} does not exist")
        elif cli and recipe.suffix == ".json" and entry.get("payloads"):
            # An XDR-JSON recipe must encode to exactly the primary payload.
            p = next(p for p in entry["payloads"] if p["role"] == "primary")
            built = xdr(cli, "encode", "--type", p["xdrType"], "--input", "json", "--output", "single", stdin=recipe.read_bytes())
            if built != (path.parent / p["file"]).read_bytes():
                errors.append(f"recipe {gen['path']} does not encode to {p['file']}")
    if cli and decoded:
        for d in entry["expect"].get("decoded", []):
            try:
                got = resolve(decoded[d.get("payload")], d["pointer"])
            except (KeyError, IndexError, ValueError) as exc:
                errors.append(f"pointer {d['pointer']}: not found ({exc!r})")
                continue
            if "equals" in d and got != d["equals"]:
                errors.append(f"pointer {d['pointer']}: got {got!r}, expected {d['equals']!r}")
            if "length" in d and len(got) != d["length"]:
                errors.append(f"pointer {d['pointer']}: length {len(got)}, expected {d['length']}")
        if entry["kind"] == "ledger":
            lg, arm = entry["ledger"], f"v{entry['ledger']['lcmVersion']}"
            hdr = decoded[None][arm]["ledger_header"]
            facts = {
                "sequence": hdr["header"]["ledger_seq"],
                "protocolVersion": hdr["header"]["ledger_version"],
                "closeTime": int(hdr["header"]["scp_value"]["close_time"]),
                "hash": hdr["hash"],
            }
            for k, v in facts.items():
                if lg[k] != v:
                    errors.append(f"ledger.{k}: manifest {lg[k]!r}, payload {v!r}")
            if "focus" in entry:
                tx = decoded[None][arm]["tx_processing"][entry["focus"]["txIndex"]]
                if tx["result"]["transaction_hash"] != entry["focus"]["txHash"]:
                    errors.append("focus.txHash does not match txProcessing[txIndex]")
    return entry, errors


def index_line(entry, path):
    line = {
        "id": entry["id"],
        "entry": f"entries/{entry['id']}/entry.json",
        "entrySha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "status": entry["status"],
        "kind": entry["kind"],
        "synthetic": entry["synthetic"],
        "caps": entry["coverage"]["caps"],
        "protocol": dict(entry["coverage"]["protocol"]),
        "rootCauses": entry["coverage"].get("rootCauses", []),
    }
    for p in entry.get("payloads", []):
        if p["role"] == "primary":
            line["primary"] = {k: p[k] for k in ("file", "xdrType", "sha256", "bytes")}
    if "ledger" in entry:
        line["protocol"]["captured"] = entry["ledger"]["protocolVersion"]
        line["ledgerSequence"] = entry["ledger"]["sequence"]
    return line


def mutations(base):
    """(description, mutated entry) pairs that the schema must reject."""

    def m(desc, fn):
        e = copy.deepcopy(base)
        fn(e)
        return desc, e

    return [
        m("synthetic flag disagrees with provenance", lambda e: e.update(synthetic=True)),
        m("active entry without payloads", lambda e: e.pop("payloads")),
        m("ledger entry without ledger facts", lambda e: e.pop("ledger")),
        m("ledger entry whose primary payload is not a LedgerCloseMeta", lambda e: e["payloads"][0].update(xdrType="TransactionEnvelope")),
        m("two primary payloads", lambda e: e["payloads"].append(dict(e["payloads"][0], name="again"))),
        m("malformed CAP tag", lambda e: e["coverage"]["caps"].append("cap-40")),
        m("participants without a focus transaction", lambda e: e.pop("focus")),
        m("payload path escaping the entry directory", lambda e: e["payloads"][0].update(file="../other/ledger.xdr")),
        m("unknown top-level key", lambda e: e.update(extra=1)),
        m("unknown root cause", lambda e: e["coverage"]["rootCauses"].append("bad-luck")),
        m("'reproduces' sighting without a pre-fix reference", lambda e: e["sightings"][0].pop("preFix")),
        m("unsupported schema major/minor", lambda e: e.update(schemaVersion="1.0.0")),
        m("network provenance without a retrieval URL", lambda e: e["provenance"].pop("retrievedFrom")),
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stellar-xdr", default=shutil.which("stellar-xdr"))
    ap.add_argument("--write-index", action="store_true")
    ap.add_argument("--check-index", action="store_true")
    args = ap.parse_args()
    cli = args.stellar_xdr
    if cli:
        print("reference:", xdr(cli, "version").decode().splitlines()[0])
    else:
        print("stellar-xdr not found: schema and digest checks only")
    failed = False
    lines, entries = [], {}
    for path in sorted(ENTRIES.glob("*/entry.json")):
        entry, errors = check_entry(path, cli)
        entries[path.parent.name] = entry
        print(f"{'FAIL' if errors else 'ok  '} {path.parent.name} [{entry.get('status')}, {entry.get('kind')}]")
        for e in errors:
            print("       ", e)
        failed |= bool(errors)
        if not errors:
            lines.append(index_line(entry, path))

    base = entries["cap0040-signed-payload-signer-added"]
    for desc, bad in mutations(base):
        rejected = any(True for _ in ENTRY.iter_errors(bad))
        print(f"{'ok  ' if rejected else 'FAIL'} rejects: {desc}")
        failed |= not rejected
    extended = dict(base, **{"x-team-note": "extension keys are allowed"})
    accepted = not any(True for _ in ENTRY.iter_errors(extended))
    print(f"{'ok  ' if accepted else 'FAIL'} accepts: x- extension key")
    failed |= not accepted

    text = ""
    for line in lines:
        failed |= bool(list(INDEX_LINE.iter_errors(line)))
        text += json.dumps(line, separators=(",", ":")) + "\n"
    if args.write_index:
        INDEX.write_text(text)
        print(f"wrote {INDEX.relative_to(ROOT)} ({len(lines)} entries)")
    elif args.check_index:
        current = INDEX.read_text() if INDEX.is_file() else ""
        print(f"{'ok  ' if current == text else 'FAIL'} corpus/index.jsonl is up to date")
        failed |= current != text
    else:
        print("\nindex.jsonl:")
        print(text, end="")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
