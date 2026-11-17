# Draft manifest schema (v0.1)

Schema: [`schema/manifest.schema.json`](../schema/manifest.schema.json) (JSON Schema 2020-12).
Entries: [`corpus/entries/`](../corpus/entries/), checked by [`tools/validate.py`](../tools/validate.py).
Background: [`design-notes.md`](design-notes.md), [`bug-catalogue.md`](bug-catalogue.md).

## Layout

```
corpus/
  index.jsonl                      generated; one line per entry
  entries/<id>/entry.json          the manifest, one per entry (this schema)
  entries/<id>/ledger.xdr          raw XDR, one value per file
```

**One manifest per entry, plus a generated index.** A new CAP adds directories and never edits a shared file, so contributions do not conflict and an entry can be reviewed, vendored or retired alone. The harness and SDK runners read `index.jsonl` line by line, filter on CAP, protocol, kind or root cause, and open only the entries they need: memory stays proportional to one entry, not to the corpus, and a per-PR run can select a subset. The index is derived, so it cannot drift; CI regenerates and compares it. Its line format is `$defs/indexLine` in the schema.

**Files are raw XDR binary**, one value each, identified by the SHA-256 of their bytes. No base64, framing or compression at rest; the harness adds those when it builds a Galexie object or a `getLedgers` response.

## Fields

| Field | Meaning |
| --- | --- |
| `schemaVersion` | `MAJOR.MINOR.PATCH` of this schema. |
| `id` | Stable ID and directory name, e.g. `cap0040-signed-payload-signer-added`. Never reused. |
| `title`, `description` | What the pattern is and why readers trip on it. |
| `status` | `planned` (ID reserved, gap documented, bytes may be missing), `active`, `retired`. |
| `kind` | `ledger`: the primary payload is one `LedgerCloseMeta`, replayable against an indexer. `value`: any other single XDR value, for SDK conformance. |
| `synthetic` | `true` exactly when the bytes did not come out of stellar-core. The schema ties it to `provenance.source`. |
| `coverage.caps` | CAPs that introduced or changed the pattern, as `CAP-0040`, `CAP-0046-11`. |
| `coverage.protocol` | `min` (first protocol in which core can emit the pattern) and optional `max`. |
| `coverage.shapes[]` | XDR types, fields and arms exercised, named as in the `.x` files (`SignerKey.ed25519SignedPayload`), each with `coreValidation`: `validated`, `partially-validated`, `unvalidated`, `core-produced`, or `to-verify` in drafts. |
| `coverage.rootCauses` | Mistake classes the entry catches: `unvalidated-passthrough`, `participants-from-envelope`, `address-width`, `meta-only-change`, `canonical-order`, `lossy-conversion`, `integer-domain`, `unbounded-resource`, `unknown-arm`, `encode-decode-asymmetry`. |
| `provenance` | One of three forms, below. |
| `payloads[]` | `name`, `role` (exactly one `primary`), `xdrType`, `file`, `sha256`, `bytes`, optional informative `json` copy. |
| `ledger` | For ledger entries: `sequence`, `protocolVersion` (the protocol the bytes were produced under), `closeTime`, `hash`, `networkPassphrase`, `lcmVersion`. |
| `focus` | `txIndex`, `txHash`, optional `opIndex`: where in the ledger the pattern is. |
| `expect` | What a correct reader must produce, below. |
| `sightings[]` | Public bugs: `url`, `component`, `relation` (`reproduces`, `covers`, `sibling`), `failure`, and `preFix` / `postFix` as `{ref, verdict}`. |
| `license`, `added`, `notes` | SPDX override, date, free text. |

### Provenance

- **`network`**: `network` (`testnet`/`pubnet`), `networkPassphrase`, `networkEpoch` (testnet is reset, so ledger numbers repeat), `ledgerSequence`, `txHash`, `retrievedFrom` (URL), `retrievedAt`, `containerSha256`.
- **`local-core`**: `core` (`version`, `commit` or `image` digest), `protocolVersion`, `networkPassphrase`, and at least one of `generator` (recipe, command, seed) and `golden` (repository, commit, path, record number, test section, file hash, and whether the payload was `copied` from stellar-core's `--check-lcm` golden or merely has the `same-shape`).
- **`synthetic`**: `method`, optional `generator` and `derivedFrom`, and two required statements: `coreCanEmit` (`yes`, `no`, `unverified`) and the `rationale` for it. The RFP wants "XDR that stellar-core would actually emit, not just XDR that decodes"; a synthetic entry has to argue that.

### Expectations

- **`roundTrip`**: the reference implementation (`rs-stellar-xdr`) and version with which every payload decodes and re-encodes to identical bytes. Every SDK is held to the same result.
- **`decoded[]`**: expected decoded values as `{pointer, equals}` or `{pointer, length}`. The pointer is a JSON Pointer into the payload's XDR-JSON (SEP-51) form, the representation rs-stellar-xdr produces. It is specified, language-neutral, and exact for 64-bit integers and non-UTF-8 bytes.
- **`participants`**: the full set of addresses the focus transaction affected, derived from its meta; and `envelopeOnly`, strkeys that appear in the envelope but are not affected accounts. Reporting one of those is the "participants from the envelope" mistake.
- **`ledgerChanges[]`**: what core changed (`entryType`, `change`, `key`). An adapter maps each to the target's own model; nothing corresponding means `dropped`.
- **`limits`**: measured depth and vector lengths, vectors at their maximum, and a memory bound, for the bounded-memory check and the guide's recommended caps.
- **`operations[]`**: named SDK operation checks (`scval-to-native`, `rebuild-from-native-string`, …) with a list of acceptable `outcomes` (`error`, `value`, `xdrHex`, or a named `invariant`). They cover defects that a plain decode does not show; an SDK runner that does not implement an operation reports `skipped`.

## How the consumers use it

| Consumer | Reads |
| --- | --- |
| Generator | `provenance` to rebuild or re-fetch; `expect` to confirm a regenerated entry is still the same pattern. |
| Replay harness | `index.jsonl` to select; `ledger` to lay out a Galexie datastore or answer `getLedgers`; `expect.participants` and `expect.ledgerChanges` for the pass / mishandled / dropped verdict; `sightings` for the pre-fix and post-fix runs. |
| Conformance runner | `payloads`, `expect.roundTrip`, `expect.decoded`, `expect.operations`; `sightings` for the pre-fix tags. |
| Linter and guide | `coverage.rootCauses` (shared with the rule IDs) and `coverage.shapes[].coreValidation`, against which the guide's field-by-field table is checked. |
| Findings report | `sightings` with `relation: sibling` link each filed issue back to its entry. |
| Coverage manifest | `coverage.caps` over all entries, including `planned` ones, gives the per-CAP view. |

## Design choices

1. **A whole `LedgerCloseMeta`, not a single transaction's meta.** It is what core emits and what Galexie and RPC serve, and it is the only object where the envelope and the meta sit side by side. `focus` points at the transaction.
2. **Protocol is a range plus the captured version.** `coverage.protocol` says when the pattern exists; `ledger.protocolVersion` says which XDR a reader needs. A capture that uses an XDR arm the pre-fix commit's XDR does not have cannot be replayed into it (measured with the `SIGNED_MS` header arm and wallet-backend#731), so the captured version decides which sightings an entry can reproduce.
3. **CAP tags are assigned, not inferred.** CAP-0073 changed no XDR and still produced two of the sightings.
4. **`synthetic` is redundant with `provenance.source` on purpose.** The RFP asks for the flag by name; the schema makes the two agree.
5. **`covers` vs `reproduces`.** An entry claims `reproduces` only with a pre-fix reference and a failure mode, and the claim is meant to be run. The two CAP-0073 samples show the difference: they hold the same ledger from two recordings of one stellar-core golden. The one whose header uses the newer `SIGNED_MS` arm cannot be decoded by wallet-backend at the #731 pre-fix commit and says `covers`; the one with the older arm reproduces the bug.
6. **Golden agreement is a reference, not a copy of the convention.** stellar-core's goldens are hash-named streams under a test passphrase; an entry points at repository, commit, path and record so CI can re-verify the bytes.
7. **Known SDK divergences are not in the manifest.** The manifest says what is correct. Which SDK version currently fails is the runner's output.

## Versioning

- `schemaVersion` is semantic. Within a major version, new fields are optional and enum values are only added; a reader must ignore keys beginning with `x-` (reserved for local extensions) and must reject a major version it does not know.
- Removing or renaming a field, or changing what a field means, is a major bump. Before 1.0 (now), a minor bump may break; the schema pins `0.1.x`.
- Entry IDs are permanent. A superseded entry becomes `retired` and stays in the index.
- Payload bytes of an `active` entry never change; a recapture is a new entry.

## Samples

| Entry | Kind, source | Sighting | Bytes |
| --- | --- | --- | --- |
| `cap0040-signed-payload-signer-added` | ledger, **testnet** ledger 4437357 (protocol 28) | wallet-backend#729, #716 (both reproduce) | 58,256 |
| `cap0073-sac-trust-creates-trustline` | ledger, **stellar-core golden** at `f2b9194`, record 43 (protocol 30) | wallet-backend#731, stellar-etl#460 (covers) | 4,504 |
| `cap0073-sac-trust-creates-trustline-signed-header` | ledger, **stellar-core golden** at `09f7d11`, record 43 (protocol 30, older header arm) | wallet-backend#731 (reproduces), stellar-etl#460 (covers) | 4,496 |
| `cap0046-scmap-keys-collide-as-native` | value (`ScVal`), **synthetic** | js-stellar-sdk#1789 (reproduces) | 48 |
| `cap0046-scmap-keys-host-order` | value (`ScVal`), **synthetic** | js-stellar-sdk#1766 (reproduces) | 124 |
| `cap0038-pool-assets-issuer-byte-order` | value (`ChangeTrustAsset`), **synthetic** | js-stellar-sdk#1766 (reproduces) | 100 |
| `cap0006-manage-buy-offer-id-above-2p53` | value (`Operation`), **synthetic** | js-stellar-sdk#1785 (reproduces) | 80 |
| `cap0027-muxed-account-id-noncanonical-input` | value (`MuxedAccount`), **synthetic** | js-stellar-sdk#1785 (reproduces) | 44 |
| `memo-text-non-utf8` | value (`Memo`), **synthetic**, unvalidated pass-through field | js-stellar-sdk#1823 (reproduces) | 12 |
| `cap0027-muxed-source-on-soroban-ttl-op` | ledger, **local core** (quickstart, core v30.0.0 at protocol 28), generated by `generator/muxed_soroban_source.mjs` | wallet-backend#753, #716 (both reproduce) | 2,284 |

`tools/validate.py` validates all ten against the schema; checks sizes and digests; decodes and re-encodes each payload with stellar-xdr 30.0.0 and compares bytes; evaluates every `expect.decoded` pointer and the `ledger` and `focus` facts against the decoded payload; checks that each generator recipe exists and that an XDR-JSON recipe (`generator/recipes/`) encodes to its payload; confirms that 13 malformed variants are rejected; and prints the index lines. All checks pass.

**All six bugs the reviewer checks name have been reproduced**, three with the replay harness and three with the conformance runner (#716 for two of its three producers); scripts, logs and limits are in [`repro/`](../repro/README.md).

- **Replay harness, wallet-backend#729.** `cap0040-signed-payload-signer-added` was replayed into the real `wallet-backend ingest` binary through a mock `getLedgers` server. At the pre-fix commit `e2436eef697f` ingestion halts on ledger 4437357 with the error from the PR and the cursor does not advance; at the fix `2c873353c1f0` the ledger is ingested and the full signer key is stored.
- **Replay harness, wallet-backend#731.** `cap0073-sac-trust-creates-trustline-signed-header` was replayed the same way. At the pre-fix commit `764a4f5bcbff` the ledger is ingested and the trustline balance is written, but no `TRUSTLINE` state change is stored (verdict `dropped`); at the fix `c152783c054c` `TRUSTLINE/ADD` and `BALANCE_AUTHORIZATION/SET` are stored. The first CAP-0073 sample, with the newer `SIGNED_MS` header arm, cannot be decoded by either commit, which is why it stays `covers`.
- **Replay harness, wallet-backend#716.** The #729 entry again, at the commit the issue was filed against (`79ea2acd1dc6`): the ledger is ingested without error and the signer key is stored cut to 33 bytes, a row that does not decode as a strkey. At `2c873353c1f0` all 41 bytes are stored. The muxed-source producer is reproduced from a ledger generated on a local core (`cap0027-muxed-source-on-soroban-ttl-op`): at the same commit the `M…` operation source is stored as a 32-byte key under the muxed version byte; before #753 (`f8e5786d22c0`) the same ledger halts ingestion on a duplicate key; at `bb0b162b0725` only the `G…` account is stored. The third producer (a muxed address in a SEP-41 event) is not reproduced.
- **Conformance runner, js-stellar-sdk#1789.** `cap0046-scmap-keys-collide-as-native` was run against the published npm packages. On v17.2.1 `scValToNative` returns one entry of two with no error; on v17.3.0 it throws. The bytes round-trip on both, so only the manifest's `expect.operations` check catches it.
- **Conformance runner, js-stellar-sdk#1766.** Two entries, one per ordering the PR fixes. On v17.2.0 the SDK refuses the valid pool pair of `cap0038-pool-assets-issuer-byte-order` (`Assets are not in lexicographic order`) and sorts the keys of `cap0046-scmap-keys-host-order` in the wrong order; on v17.2.1 it returns the payload bytes for both.
- **Conformance runner, js-stellar-sdk#1785.** Two entries. On v17.2.1 an offer ID of 2^53 + 1 passed as a number is encoded as 2^53, and the strings `""`, `"-1"`, `"0x10"` are accepted as offer or muxed IDs; on v17.3.0 each is refused. The manifest carries the inputs in `expect.operations[].args`.

The wallet-backend verdicts are computed by an adapter ([`repro/wb_verdict.py`](../repro/wb_verdict.py)) from each entry's `ledger`, `focus`, `expect.participants` and `expect.ledgerChanges`, with nothing passed by hand. `memo-text-non-utf8` (js-stellar-sdk#1823) was run too: it diverges on v17.2.1 and still on v17.3.0, because upstream documented the lossy getter and kept the behaviour.

```
python3 -m venv .venv && .venv/bin/pip install jsonschema
cargo install --locked stellar-xdr --version 30.0.0 --features cli
.venv/bin/python tools/validate.py
```
