# Reproductions from corpus entries

All six bugs the RFP's reviewer checks name, each reproduced from sample entries at a pre-fix version and checked against the fix. Run on 2026-11-17.

| Bug | Tool | Entry | Pre-fix | Fixed |
| --- | --- | --- | --- | --- |
| [wallet-backend#729](#wallet-backend729) | replay harness | `cap0040-signed-payload-signer-added` | mishandled (halt) | pass |
| [wallet-backend#731](#wallet-backend731) | replay harness | `cap0073-sac-trust-creates-trustline-signed-header` | dropped | pass |
| [wallet-backend#716](#wallet-backend716) | replay harness | `cap0040-signed-payload-signer-added` | mishandled (truncated key) | pass |
| [wallet-backend#716](#wallet-backend716), muxed source | replay harness | `cap0027-muxed-source-on-soroban-ttl-op` | mishandled (truncated participant) | pass |
| [wallet-backend#753](#wallet-backend753-and-the-muxed-source-producer-of-716) (not in the reviewer checks) | replay harness | `cap0027-muxed-source-on-soroban-ttl-op` | mishandled (halt) | pass |
| [js-stellar-sdk#1789](#js-stellar-sdk1789) | conformance runner | `cap0046-scmap-keys-collide-as-native` | diverged | pass |
| [js-stellar-sdk#1766](#js-stellar-sdk1766) | conformance runner | `cap0038-pool-assets-issuer-byte-order`, `cap0046-scmap-keys-host-order` | diverged | pass |
| [js-stellar-sdk#1785](#js-stellar-sdk1785) | conformance runner | `cap0006-manage-buy-offer-id-above-2p53`, `cap0027-muxed-account-id-noncanonical-input` | diverged | pass |

Not everything in each bug is covered; every section ends with its limits. The largest gap: #716 lists three producers; two are reproduced (signed-payload signer, muxed Soroban source) and the third (muxed address in a SEP-41 event) is not.

## wallet-backend#729

Run on 2026-11-17 (macOS arm64, Go 1.25.9, TimescaleDB 2.28.2-pg17 in Docker).

**Result: the corpus entry `cap0040-signed-payload-signer-added` halts wallet-backend before the fix and is ingested after it.**

| | Pre-fix `e2436eef697f` | Post-fix `2c873353c1f0` (merge of [#729](https://github.com/stellar/wallet-backend/pull/729)) |
| --- | --- | --- |
| Verdict | **mishandled** (halt) | **pass** |
| Ingest cursor after the run | 4437356 (did not advance) | 4437357 |
| Transactions stored for the ledger | 0 of 12 | 12 of 12 |
| Signer state changes stored | 0 | 1, key of 41 bytes |
| Process | 5 persist attempts over about 15 s, then exits with a panic | logs `Ingested ledger 4437357`, then waits for the next ledger |

The pre-fix error, from [`evidence/pre-fix-729.ingest.log`](evidence/pre-fix-729.ingest.log):

```
pgx CopyFrom state_changes: ERROR: COPY from stdin failed: converting signer_account_id:
converting address to bytes: stellar address PBCK2RYY…B55LN6AANMS has a 40-byte key payload;
the account_id column stores only 32-byte account/contract keys (SQLSTATE 57014)
```

This is the failure the PR describes: the signed-payload signer key is carried into a column that accepts 32-byte keys only, the COPY fails, the transaction rolls back with the cursor update, and the same ledger is retried. The post-fix binary stores `78 ‖ ed25519 key ‖ 00000003 ‖ deadbe00`: the `P…` version byte and the full 40-byte key, as the PR says it should.

### What was run

- **Input**: `corpus/entries/cap0040-signed-payload-signer-added/ledger.xdr`, the real testnet ledger 4437357, unmodified (the runner checks its SHA-256 against the manifest).
- **Target**: the real `wallet-backend ingest` binary in live mode, built from each commit with `go build`, with no source changes.
- **Ledger source**: [`mock_rpc.py`](mock_rpc.py), a standard-library JSON-RPC server that answers `getHealth` and `getLedgers` from the entry's manifest. wallet-backend called each once (`evidence/*.rpc.log`).
- **Database**: an empty TimescaleDB, schema created by `wallet-backend migrate up` of the same commit.
- **Verdict**: read from the database, not from the logs, by [`wb_verdict.py`](wb_verdict.py). Every expectation comes from the entry: the cursor must reach `ledger.sequence`; the focus transaction must be stored; its stored participants must include `expect.participants.addresses` and contain no malformed key and no `envelopeOnly` address; and each `expect.ledgerChanges` item the adapter can map must have its row (a created trustline needs a `TRUSTLINE/ADD` state change; a signer the meta shows added needs a `SIGNER/ADD` state change holding the full key, which the adapter reads from the entry's own payload). Nothing is passed on the command line.

No network access and no keys are used at run time.

### Two things the harness had to do

1. **Seed the ingest cursor.** With an empty database, live mode loads state from a history-archive checkpoint. The runner inserts `latest_ingest_ledger = 4437356` so ingestion resumes at the entry, and points `--archive-url` at an empty local directory. The post-fix run passing under the same seeding shows the seeding is not what causes the failure.
2. **Use mock RPC, not the Galexie layout.** wallet-backend's datastore backend is S3-only (`internal/ingest/ledger_backend.go` hard-codes `Type: "S3"`), so a local directory is not accepted. A Galexie replay needs an S3-compatible endpoint; the mock `getLedgers` path needs nothing.

### Limits of this evidence

- The account balances the ledger touches are not in the database (no checkpoint was loaded). Neither binary complained.
- The adapter maps two kinds of ledger change (created trustline, added signer) and participants. Other entry types in `expect.ledgerChanges` are reported as `not-mapped`, not checked; balances and the other state-change categories are not compared.
- The halt is reproduced through live ingestion. The PR also names backfill; not run.

### Run it again

```
docker run -d --name xdr-repro-db -p 127.0.0.1:55432:5432 \
  -e POSTGRES_HOST_AUTH_METHOD=trust timescale/timescaledb:2.28.2-pg17 \
  postgres -c timescaledb.enable_chunk_skipping=on -c timescaledb.enable_sparse_index_bloom=on

git clone https://github.com/stellar/wallet-backend && cd wallet-backend
git checkout e2436eef697fc2e97eaf84895ed6dff5f5efd908 && go build -o ../wb-pre .
git checkout 2c873353c1f0f3c9b145e25a7d009023ad104a1c && go build -o ../wb-post .
cd ..

E=corpus/entries/cap0040-signed-payload-signer-added
repro/run_wallet_backend.sh pre-fix  "$PWD/wb-pre"  "$PWD/$E" "$PWD/out"
repro/run_wallet_backend.sh post-fix "$PWD/wb-post" "$PWD/$E" "$PWD/out"
docker rm -f xdr-repro-db
```

Set `STELLAR_XDR` to the stellar-xdr CLI if it is not on `PATH`. Each run prints a result like [`evidence/pre-fix-729.result.json`](evidence/pre-fix-729.result.json). Ports and the database URL can be changed with `RPC_PORT`, `INGEST_PORT`, `PGURL_BASE` and `DB_CONTAINER`.

## wallet-backend#731

Run on 2026-11-17, same setup as #729 (real `wallet-backend ingest` binary, mock `getLedgers`, empty TimescaleDB, seeded cursor).

**Result: the corpus entry `cap0073-sac-trust-creates-trustline-signed-header` is ingested before the fix without any trustline state change, and with one after it.**

| | Pre-fix `764a4f5bcbff` | Post-fix `c152783c054c` (merge of [#734](https://github.com/stellar/wallet-backend/pull/734)) |
| --- | --- | --- |
| Verdict | **dropped** | **pass** |
| Ingest cursor after the run | 44 (advanced) | 44 |
| Transactions stored | 1 of 1 | 1 of 1 |
| State changes stored for the ledger | `BALANCE/DEBIT` only | `BALANCE/DEBIT`, `TRUSTLINE/ADD`, `BALANCE_AUTHORIZATION/SET` |
| `trustline_balances` rows | 1 | 1 |

This is the gap [the issue](https://github.com/stellar/wallet-backend/issues/731) describes: `trustline_balances` is correct, so nothing fails, but the history has no entry that explains the balance. Unlike #729 there is no error and no log line; the only way to see it is to compare what was stored with what the ledger meta says changed. The adapter does that from the entry: `expect.ledgerChanges` lists a created trustline, so a `TRUSTLINE/ADD` state change for that account must exist.

### The protocol-version problem, measured

The first CAP-0073 sample (`cap0073-sac-trust-creates-trustline`, from stellar-core `f2b91947`) could not be used. Its header carries the `SIGNED_MS` StellarValue arm, and wallet-backend at **both** commits (go-stellar-sdk v0.7.3) rejects it: `decoding StellarValueType: '3' is not a valid StellarValueType enum value`. Ingestion does not advance (`evidence/*-731-signed-ms.result.json`).

The entry that reproduces the bug is record 43 of the same golden file one commit earlier (stellar-core `09f7d11b5f1d`), copied byte for byte. It decodes to the same ledger and the same transaction `eab3ffad…5cfe`; only the header differs (StellarValue arm and the hashes that depend on it). No bytes were edited.

### Limits of this evidence

- The ledger comes from a stellar-core test (passphrase `(V) (;,,;) (V)`, ledger 44), not from a public network. wallet-backend was run with that passphrase.
- Live ingestion only.

### Run it again

Build the two commits as for #729 (`764a4f5bcbff5abb0949d93da37f769063013ea0`, `c152783c054c765c8b51fd26ed313fd1ebc1c50c`), then:

```
E=corpus/entries/cap0073-sac-trust-creates-trustline-signed-header
repro/run_wallet_backend.sh pre-fix-731  "$PWD/wb-pre731"  "$PWD/$E" "$PWD/out"
repro/run_wallet_backend.sh post-fix-731 "$PWD/wb-post731" "$PWD/$E" "$PWD/out"
```

## wallet-backend#716

Run on 2026-11-17, same setup as #729.

**Result: at the commit the issue was filed against, the corpus entry `cap0040-signed-payload-signer-added` is ingested with its signer key silently cut to 33 bytes; after #729 the full 41 bytes are stored.**

| | `79ea2acd1dc6` (the issue's commit, #715) | `2c873353c1f0` (merge of #729) |
| --- | --- | --- |
| Verdict | **mishandled** (truncated key) | **pass** |
| Ingest cursor after the run | 4437357 (advanced, no error) | 4437357 |
| `SIGNER/ADD` state change stored | 1 | 1 |
| `signer_account_id` bytes | 33: `78 ‖ ed25519 key` | 41: `78 ‖ ed25519 key ‖ 00000003 ‖ deadbe00` |

The 33-byte row is what the issue calls an undecodable row. Encoded back as a strkey it gives `PBCK2RYY…NSBYVM`, and go-stellar-sdk's `strkey.DecodeAny` rejects that with `invalid signed payload length 32, expected at least 36`. The payload `deadbe` is gone and cannot be recovered from the database.

The same ledger therefore shows three behaviours over three commits: silent truncation at `79ea2acd1dc6` (#716), a halt at `e2436eef697f` once a length guard was added (#729), and a correct row at `2c873353c1f0`. The adapter tells them apart without help: it reads the added signer from the entry's ledger meta, expects its full 41-byte key in a `SIGNER/ADD` state change, and finds 33 bytes.

### Limits of this evidence

- **Two of three producers.** This section covers signed-payload signers; the muxed (`M…`) source on a Soroban operation is reproduced [below](#wallet-backend753-and-the-muxed-source-producer-of-716) from a ledger generated on a local core. Not reproduced: a muxed `SCAddress` in a SEP-41 event, which needs another generated ledger and wallet-backend's SEP-41 protocol setup.
- The issue was closed without a linked PR. The post-fix reference used here is the #729 merge, which is the fix for this producer only.

### Run it again

Build `79ea2acd1dc65a07166133cc04b12e76db314db0` as for #729, then:

```
E=corpus/entries/cap0040-signed-payload-signer-added
repro/run_wallet_backend.sh pre-fix-716-signer  "$PWD/wb-pre716" "$PWD/$E" "$PWD/out"
repro/run_wallet_backend.sh post-fix-716-signer "$PWD/wb-post"   "$PWD/$E" "$PWD/out"
```

## wallet-backend#753 and the muxed-source producer of #716

Run on 2026-11-17. The ledger was generated for this run on a local stellar-core; the replay setup is the same as for #729.

**Result: one generated ledger, `cap0027-muxed-source-on-soroban-ttl-op`, shows both bugs and the fix.**

| | `79ea2acd1dc6` (#716's commit) | `f8e5786d22c0` (before #753) | `bb0b162b0725` (merge of [#753](https://github.com/stellar/wallet-backend/pull/753)) |
| --- | --- | --- | --- |
| Verdict | **mishandled** (truncated participant) | **mishandled** (halt) | **pass** |
| Ingest cursor after the run | 30 (advanced, no error) | 29 (did not advance) | 30 |
| Participant keys stored | 2: `30 ‖ key` (the `G…` account) and `60 ‖ key` (an `M` version byte on a 32-byte key) | none | 1: `30 ‖ key` |
| Process | keeps running | exits: `duplicate key value violates unique constraint "…transactions_accounts_pkey" (SQLSTATE 23505)` | keeps running |

- At `79ea2acd1dc6` the `M…` operation source is stored cut to 32 bytes under the muxed version byte. That is producer 1 of #716: a row that is not a valid address, and the mux ID is gone.
- At `f8e5786d22c0` the encoder reduces `M…` and `G…` to the same key, the participant COPY carries two rows with one primary key, and ingestion stops, as #753 describes.
- At `bb0b162b0725` the only participant is the `G…` account, which is what the ledger meta shows changing.

### How the ledger was generated

[`generator/muxed_soroban_source.mjs`](../generator/muxed_soroban_source.mjs), against `stellar/quickstart` (core v30.0.0, image digest in the entry) started with `--local --protocol-version 28 --enable core,rpc`. The script funds a test account from the network root account, deploys the native asset contract, submits an `ExtendFootprintTTL` on that contract's instance entry with an `M…` operation source (mux ID 1234567890123456789), and saves the `LedgerCloseMeta` of the ledger it landed in. Core applied it successfully. No external keys or funds: the root account comes from the passphrase and the test seed is a fixed hash. The run's record is `evidence/gen-muxed-soroban-source.recipe-run.json`.

Three things the generator has to deal with, all found here:

1. **Protocol 28, not 30.** Held at protocol 28, core v30 writes the `SIGNED` header arm that these wallet-backend commits decode (see #731).
2. **No simulation.** The quickstart RPC (v30) answers `simulateTransaction` with a host internal error on a protocol-28 network, so the recipe sets Soroban resources by hand.
3. **The SDK will not build the transaction.** js-stellar-sdk 17.3.0 throws `Soroban operations cannot use a muxed source account`, although core accepts it for `ExtendFootprintTTL`. The recipe sets the source on the envelope before signing. A generator cannot rely on an SDK's builders for shapes the SDK considers invalid.

### Limits of this evidence

- The bytes are not reproducible: close time and ledger number change on every run. The recipe was run twice, the second time end to end on a fresh network (funding in ledger 28, deployment in 29, the muxed-source transaction in 30); both runs gave a 2,284-byte ledger of the same shape and the same three verdicts. The committed payload and the table above are from the second run.
- The entry's run-specific facts (ledger hash, sequence, close time, transaction hash) were updated from the new bytes by a one-off script; the generator does not write the manifest yet.
- On a network that has just started, the first transaction from the root account can be refused or never confirm; the recipe retries the funding step.
- #753 is not one of the six bugs the reviewer checks name.

### Run it again

```
docker run -d --name xdr-gen-core -p 127.0.0.1:18000:8000 stellar/quickstart --local --protocol-version 28 --enable core,rpc
node generator/muxed_soroban_source.mjs sdk-17.3.0 http://127.0.0.1:18000/rpc out/gen     # new ledger.xdr
docker rm -f xdr-gen-core

E=corpus/entries/cap0027-muxed-source-on-soroban-ttl-op
repro/run_wallet_backend.sh muxed-source-pre716  "$PWD/wb-pre716"  "$PWD/$E" "$PWD/out"
repro/run_wallet_backend.sh muxed-source-pre753  "$PWD/wb-pre753"  "$PWD/$E" "$PWD/out"
repro/run_wallet_backend.sh muxed-source-post753 "$PWD/wb-post753" "$PWD/$E" "$PWD/out"
```

Commits: `79ea2acd1dc65a07166133cc04b12e76db314db0`, `f8e5786d22c0` and `bb0b162b072583c7dabffbdee664d1c117e5ba56`.

## js-stellar-sdk#1789

Run on 2026-11-17 (Node v26.13.1), with the published npm packages.

**Result: the corpus entry `cap0046-scmap-keys-collide-as-native` loses a map entry in `scValToNative` on v17.2.1 and raises an error on v17.3.0.**

| | Pre-fix `@stellar/stellar-sdk@17.2.1` | Fixed `@stellar/stellar-sdk@17.3.0` |
| --- | --- | --- |
| Verdict | **diverged** (silent drop) | **pass** |
| Decode and re-encode of the 48 bytes | identical | identical |
| `scValToNative` on the 2-entry map | returns `{ "1": 20 }`: one entry, no error | throws `scvMap has more than one key that converts to "1"; read the raw map entries instead` |

The entry's manifest accepts two outcomes for the `scval-to-native` operation: an error, or a value that still has 2 entries. v17.2.1 gives neither; the `u32 1 → 10` entry is gone. Results: [`evidence/js-sdk-1789-v17.2.1.result.json`](evidence/js-sdk-1789-v17.2.1.result.json), [`evidence/js-sdk-1789-v17.3.0.result.json`](evidence/js-sdk-1789-v17.3.0.result.json).

### What was run

[`run_js_sdk.mjs`](run_js_sdk.mjs) reads `entry.json`, checks the payload digest, decodes `value.xdr` with the installed SDK's `xdr.ScVal.fromXDR`, compares the re-encoded bytes, then runs each `expect.operations` item and compares the result with the manifest's accepted outcomes. The expected result is in the manifest, not in the runner; an operation the runner does not implement is reported as `skipped`.

### Limits of this evidence

- The byte round trip passes on both versions: this defect is only visible through the native conversion, which is why the manifest has `expect.operations`.
- `memo-text-non-utf8` (js-stellar-sdk#1823, not in the reviewer checks) was also run: reading the memo through the `.value` getter and rebuilding it gives `0000000100000003efbfbd00` instead of the payload on v17.2.1 **and** on v17.3.0. Upstream resolved the issue by documenting the getter as lossy, so the entry records `diverged` for both; it marks a documented hazard (`evidence/js-sdk-1823-*.result.json`).
- JavaScript only; the Go and Rust runners do not exist yet.

### Run it again

```
for v in 17.2.1 17.3.0; do
  mkdir -p sdk-$v && (cd sdk-$v && npm init -y >/dev/null && npm install --ignore-scripts @stellar/stellar-sdk@$v)
  node repro/run_js_sdk.mjs sdk-$v corpus/entries/cap0046-scmap-keys-collide-as-native
done
```

## js-stellar-sdk#1766

Run on 2026-11-17 (Node v26.13.1), with the published npm packages. The PR fixes two orderings, so there are two entries.

**Result: both entries diverge on v17.2.0 and pass on v17.2.1.**

| Entry and check | Pre-fix `@stellar/stellar-sdk@17.2.0` | Fixed `@stellar/stellar-sdk@17.2.1` |
| --- | --- | --- |
| `cap0038-pool-assets-issuer-byte-order`: build the pool-share asset from the payload's two assets | **diverged**: throws `Assets are not in lexicographic order` on a valid pair | **pass**: returns the payload's 100 bytes |
| Same entry: pool ID | **diverged**: same error | **pass**: `5c2ee6fa…6bc7`, the SHA-256 of the pool parameters computed independently |
| `cap0046-scmap-keys-host-order`: sort the map's six entries, given in reverse | **diverged**: returns `bytes 0a, bytes 09, symbol a, string b, i32 -1, i32 1` | **pass**: returns the payload's order `i32 -1, i32 1, bytes 09, bytes 0a, string b, symbol a` |
| Decode and re-encode, both entries | identical | identical |

- **Pool entry.** Two `USD` assets whose issuers are `61 67 …` (`GBQW…`) and `74 a1 …` (`GB2K…`). As bytes the first is lower, which is the order the protocol requires; as strkey text it is higher, because base32 digits sort before letters in ASCII. v17.2.0 compares the text and refuses the pair.
- **Map entry.** v17.2.0 gets every pair wrong: the two byte strings, the string and the symbol, and the integers after the byte keys. The PR says the host rejects such a map.

Results: `evidence/js-sdk-1766-*.result.json`.

### What was added to the runner

Three operations: `scval-map-sort`, `liquidity-pool-build`, `liquidity-pool-id`. Each has a `prepare` step (runner code that reads the decoded payload) and a `run` step (the SDK call under test). Only a throw in `run` counts as the SDK's error outcome; a failure in `prepare` aborts the run. This matters: a first version of the runner used the wrong accessors for v17's XDR objects and would have reported its own mistake as an SDK divergence.

### Limits of this evidence

- Both payloads are synthetic values, encoded with the stellar-xdr CLI, not ledger output. For the pool entry `coreCanEmit` is `unverified`: the pair is in byte order and the issuers are ordinary ed25519 keys, but it has not been submitted to core. For the map entry the host order follows the rule stated in the PR and matches v17.2.1; it has not been checked against a running host.
- The map check is that the SDK's sort reproduces the payload order. If our reading of the host order were wrong, the fixed SDK would disagree with the payload, and it does not; that is agreement between two sources, not proof.
- `nativeToScVal`, the third change in the PR, is not exercised.

### Run it again

```
for v in 17.2.0 17.2.1; do
  mkdir -p sdk-$v && (cd sdk-$v && npm init -y >/dev/null && npm install --ignore-scripts @stellar/stellar-sdk@$v)
  node repro/run_js_sdk.mjs sdk-$v corpus/entries/cap0038-pool-assets-issuer-byte-order
  node repro/run_js_sdk.mjs sdk-$v corpus/entries/cap0046-scmap-keys-host-order
done
```

## js-stellar-sdk#1785

Run on 2026-11-17 (Node v26.13.1), with the published npm packages. The issue covers offer IDs and muxed account IDs, so there are two entries.

**Result: both entries diverge on v17.2.1 and pass on v17.3.0.**

| Entry and check | Pre-fix `@stellar/stellar-sdk@17.2.1` | Fixed `@stellar/stellar-sdk@17.3.0` |
| --- | --- | --- |
| `cap0006-manage-buy-offer-id-above-2p53`: rebuild with the ID 2^53 + 1 as a number | **diverged**: encodes `…0020000000000000` (2^53), a different offer, no error | **pass**: throws `offerId must be a non-negative safe integer…` |
| Same entry: ID `""`, `"-1"`, `"0x10"` | **diverged**: encodes 0, −1 and 16 | **pass**: throws for each |
| Same entry: ID as the decimal string (control) | pass | pass |
| `cap0027-muxed-account-id-noncanonical-input`: ID `"0x10"`, `" 16 "` | **diverged**: accepted; the address encodes 16 but `id()` returns the text as typed | **pass**: throws |
| Same entry: ID `""` | **diverged**: accepted as ID 0 | **pass**: throws |
| Same entry: ID `"016"` | **diverged**: `id()` returns `"016"` | **pass**: accepted and normalised to `"16"` |
| Same entry: ID `"16"` (control) | pass | pass |
| Decode and re-encode, both entries | identical | identical |

Results: `evidence/js-sdk-1785-*.result.json`.

### What was added to the runner

Three operations (`offer-id-from-decimal-string`, `offer-id-from-float`, `muxed-id-parse`) and operation arguments: the manifest's `expect.operations[].args` carries the input, so one entry lists several inputs against the same payload.

### Limits of this evidence

- This bug is input validation on the build side. The payloads are synthetic; they give the checks a fixed, valid target, and the defect never shows in decoding.
- `offer-id-from-float` is specific to languages whose default number is a double. A Go or Rust runner would report it as `skipped`.
- `manageSellOffer` is not exercised by an entry (the same pre-fix behaviour was seen by hand), and `"016"` is accepted by the fixed SDK; the manifest allows that as long as the reported ID is `"16"`.
- For the offer entry `coreCanEmit` is `unverified`.

### Run it again

```
for v in 17.2.1 17.3.0; do
  node repro/run_js_sdk.mjs sdk-$v corpus/entries/cap0006-manage-buy-offer-id-above-2p53
  node repro/run_js_sdk.mjs sdk-$v corpus/entries/cap0027-muxed-account-id-noncanonical-input
done
```
