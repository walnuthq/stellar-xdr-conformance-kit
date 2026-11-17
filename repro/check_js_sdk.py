#!/usr/bin/env python3
"""Check every js-stellar-sdk verdict recorded in the corpus.

For each value entry with a `reproduces` sighting on stellar/js-stellar-sdk,
install the pre-fix and post-fix releases named in the manifest from npm, run
the entry through run_js_sdk.mjs, and compare the runner's verdict with the one
the manifest records.

Usage: check_js_sdk.py [--sdk-cache DIR]      (needs node and npm; npm needs network)
Exit code 1 if any measured verdict differs from the manifest.
"""
import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUNNER = ROOT / "repro" / "run_js_sdk.mjs"
PACKAGE = "@stellar/stellar-sdk"


def sdk_dir(cache, version):
    d = cache / version
    if not (d / "node_modules" / PACKAGE / "package.json").is_file():
        d.mkdir(parents=True, exist_ok=True)
        (d / "package.json").write_text('{"private": true}\n')
        subprocess.run(
            ["npm", "install", "--silent", "--no-audit", "--no-fund", "--ignore-scripts", f"{PACKAGE}@{version}"],
            cwd=d, check=True,
        )
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sdk-cache", default=str(ROOT / ".sdk-cache"))
    cache = pathlib.Path(ap.parse_args().sdk_cache)
    failed = False
    for path in sorted((ROOT / "corpus" / "entries").glob("*/entry.json")):
        entry = json.loads(path.read_text())
        if entry["kind"] != "value" or entry["status"] != "active":
            continue
        for s in entry.get("sightings", []):
            if s["component"] != "stellar/js-stellar-sdk" or s["relation"] != "reproduces":
                continue
            for side in ("preFix", "postFix"):
                if side not in s:
                    continue
                version, want = s[side]["ref"].lstrip("v"), s[side]["verdict"]
                out = subprocess.run(
                    ["node", str(RUNNER), str(sdk_dir(cache, version)), str(path.parent)],
                    capture_output=True, text=True,
                )
                got = json.loads(out.stdout)["verdict"] if out.returncode == 0 else f"runner error: {out.stderr.strip().splitlines()[-1]}"
                ok = got == want
                failed |= not ok
                issue = s["url"].rsplit("/", 1)[-1]
                print(f"{'ok  ' if ok else 'FAIL'} {entry['id']} #{issue} {side} {version}: {got}" + ("" if ok else f" (manifest says {want})"))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
