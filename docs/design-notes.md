# Design notes: findings behind the corpus and manifest

Compiled 2026-11-17, before and while the sample entries and reproductions were built. The schema itself is explained in [`manifest-schema.md`](manifest-schema.md).
Unless a line says "from memory" or "not verified", it was checked today against the source named next to it.
Repository states used: stellar-xdr `4f524bb` (main), rs-stellar-xdr `be9d4ed` (v30.0.0), stellar-core `f2b9194`, stellar-protocol `379a0b5`, go-stellar-sdk `578d9e7`, wallet-backend `3e22515`, js-stellar-sdk `5243062`.

## 1. What the RFP requires of the corpus and manifest

Source: https://stellar.gitbook.io/scf-handbook/scf-awards/build-award/rfp-track#current-open-rfps (fetched as markdown by appending `.md` to the page URL). Quotes are exact.

| Topic | RFP wording | Consequence for the manifest |
| --- | --- | --- |
| Manifest fields | "XDR files plus a manifest (entry ID, CAP, protocol version, source, synthetic flag, expected decoded values)" | These six are required fields. |
| Granularity | "a corpus of rare ledger and transaction patterns, one entry per pattern, tagged with the CAP that introduced it" | One entry = one pattern; an entry can list several CAPs. |
| CAP tagging | "tags every entry with CAP number and protocol version, and can grow as new CAPs ship" | CAP list and protocol range per entry; planned entries make gaps visible. |
| Provenance | "Corpus entries are XDR that stellar-core would actually emit, not just XDR that decodes. Entries are captured from testnet or mainnet history or produced by a local stellar-core; hand-crafted entries are marked synthetic in the manifest." | Three provenance kinds; synthetic entries must say whether core could emit them. |
| Reproducibility | "Generation is reproducible for both testnet and mainnet passphrases." | Passphrase is recorded per entry; the generator takes it as a parameter. |
| Round trip | "Check: entries round-trip through rs-stellar-xdr and agree with --check-lcm goldens." | Reference implementation and version recorded; a link to the stellar-core golden where one exists. |
| Goldens | "Where stellar-core publishes golden LedgerCloseMeta for its own CI (the --check-lcm mode), the corpus should agree with it." | See section 7. |
| Expected values | "Each corpus entry carries its expected decoded values, so an SDK can check both that it decodes the entry and that re-encoding it reproduces the original bytes." | Assertions on decoded values plus a byte-identical round trip. |
| Named shapes | "asset pairs whose issuers sort differently by strkey and by XDR bytes, muxed source and recipient accounts, muxed account and offer IDs that decode to a different ID than they encode, vectors at their maximum length, nested authorization entries, and maps whose keys collide after conversion" | Each must be expressible; some need SDK operation checks, not only bytes. |
| Language neutrality | "The corpus is language-neutral: XDR files plus a manifest any pipeline can read." | JSON manifest, raw XDR files, no language-specific fixtures. |
| Harness | "per-entry pass / mishandled / dropped report"; "delivers ledgers through an input existing pipelines already consume, such as the Galexie data lake layout or a mock RPC getLedgers endpoint; proposals should say which one they will support first" | Ledger facts in the manifest so both layouts can be built from one entry. |
| Harness check | "reference adapter reproduces wallet-backend#729, #731 and #716 on pre-fix commits and passes on the fixed ones" | Pre-fix and post-fix references per sighting. |
| Conformance | "divergence matrix across the JS, Go and Rust SDKs. Check: reproduces js-stellar-sdk#1766, #1785 and #1789 on pre-fix tags." | Same, with tags. |
| Guide | "the field-by-field table agrees with the corpus manifest" | The manifest records, per field, what core validates. |
| Linter | "trusting unvalidated fields, missing depth and size caps on recursive structures such as authorization trees and nested ScVals, and deriving participants from the envelope instead of the meta" | Root-cause tags shared by entries, linter rules and guide. |
| Findings | "one entry per finding (issue link, corpus entry, severity, component affected)" | Sibling issues link back to entries. |
| Non-functional | "run offline, need no mainnet keys or funds, and are fast enough to run in CI on every pull request"; "handles a full corpus without unbounded memory growth"; "Open source under a permissive license" | Bytes are committed; the index is streamable; licence per entry. |
| Proposal | "a draft manifest schema, two or three sample corpus entries, and evidence of reproducing one sighting from section 7. Proposals without these are incomplete." | The sightings are in section 6 of the RFP; there is no section 7. |

## 2. Bugs

See [`bug-catalogue.md`](bug-catalogue.md). The RFP lists 18 sightings, of which 16 are defects.

## 3. XDR surface

Source: stellar-xdr `.x` files. The `main` branch is the source of truth; `curr` and `next` are generated from it by evaluating `#ifdef` feature flags (README). Today the only flag on `main` is `TEST_FEATURE`, so `curr` and `next` are the same protocol-30 XDR.

- `LedgerCloseMeta` is a union with arms v0, v1, v2 (`Stellar-ledger.x:695`). v2 carries the header, the `GeneralizedTransactionSet`, `txProcessing` in apply order, upgrades and SCP info.
- `TransactionMeta` has arms v0 to v4 (`Stellar-ledger.x:560`). v4 has per-operation events (`OperationMetaV2`), transaction-level events and diagnostic events.
- `LedgerCloseMetaBatch` (`Stellar-exporter.x`): `startSequence`, `endSequence`, `ledgerCloseMetas<>`. This is what Galexie writes.
- Arms relevant to the sightings, confirmed present: `SignerKey.ed25519SignedPayload`; `CryptoKeyType.KEY_TYPE_MUXED_ED25519 = 0x100`; `SCAddressType` 0-5 (account, contract, muxed account, claimable balance, liquidity pool, muxed contract); `SorobanCredentialsType` 0-3 (source account, address, address v2, address with delegates); `StellarValueType` 0-4 (basic, signed, empty tx set, signed ms, empty tx set ms); `ContractExecutableType` 0-2 (wasm, stellar asset, external ref); `Memo.text` is `string<28>`.

**Decision: an entry is one `LedgerCloseMeta` or one other XDR value.**

- `kind: ledger`: one whole `LedgerCloseMeta`. It is the unit core emits, the unit Galexie and `getLedgers` serve, and the only place where envelope, result and meta sit together, which the participants-from-meta rule needs. Cutting out a single `TransactionMeta` would lose the envelope it must be compared with. A `focus` (transaction and operation index) says where the pattern is.
- `kind: value`: any other single top-level value (`ScVal`, `Memo`, `TransactionEnvelope`, `MuxedAccount`, …) for SDK conformance, where a 48-byte vector is easier to reason about than a ledger.
- Real ledgers contain unrelated transactions (the testnet sample has 12). That is acceptable and even useful; the manifest's assertions target the focus transaction.

**Validated vs passed through.** The RFP gives the rule ("signatures, sequence numbers, fees, time bounds" validated; "memos, Soroban authorization trees, muxed account IDs" passed through). From the sightings: memo text bytes need not be UTF-8 (js-stellar-sdk#1823); a muxed source is rejected only on `InvokeHostFunction` from protocol 25 and is valid on `ExtendFootprintTTL` and `RestoreFootprint` (wallet-backend#753, js-stellar-sdk#1906); maps with keys of mixed types are valid if host-ordered (js-stellar-sdk#1766, #1778). We have **not** yet read stellar-core's validation code field by field. The schema therefore allows `coreValidation: "to-verify"` in drafts, and the field table is milestone work reviewed by a core engineer.

Consequence for provenance: anything core validates or produces can only be captured (network or local core). Bare values and encoder-side shapes are synthetic by nature and must state `coreCanEmit`.

## 4. CAPs, protocol versions and XDR changes

Source: `stellar-protocol/core/cap-*.md` headers (`Status:`, `Protocol version:`). 102 CAP files. Protocol 30 is the current release (stellar-core v30.0.0, 2026-10-16); CAP-0089 and CAP-0090 are drafts for 31.

CAPs that matter to the corpus, with the protocol from the CAP header. The "XDR / shape" column is our reading, to be machine-checked when the coverage manifest is built.

| CAP | Protocol | XDR / shape |
| --- | --- | --- |
| CAP-0015 | 13 | Fee-bump envelope |
| CAP-0027 | 13 | `MuxedAccount` |
| CAP-0023 | 14 | Claimable balances, recursive `ClaimPredicate` |
| CAP-0033 | 14/15 | Sponsorship extensions and operations |
| CAP-0035 | 17 | Clawback |
| CAP-0038 | 18 | Liquidity pools, pool-share trustlines |
| CAP-0021 | 19 | `PreconditionsV2`, extra signers |
| CAP-0040 | 19 | Signed-payload signer |
| CAP-0042 | 20 | `GeneralizedTransactionSet` |
| CAP-0046 (-01 to -12) | 20 | Soroban: `SCVal`, `SCAddress`, `InvokeHostFunctionOp`, authorization entries, contract data/code/TTL entries, `TransactionMetaV3`, `LedgerCloseMetaV1` |
| CAP-0058 | 22 | Constructors (`CreateContractArgsV2`) |
| CAP-0062, CAP-0063, CAP-0066, CAP-0067 | 23 | Parallel tx-set phases, `LedgerCloseMetaV2`, `TransactionMetaV4`, unified events, new `SCAddress` kinds |
| CAP-0073 | 26 | **No XDR change**: SAC `trust()` and account creation by SAC transfer |
| CAP-0077 | 26 | Frozen ledger keys (config setting) |
| CAP-0071 (-01, -02) | 27 | New `SorobanCredentials` arms |
| CAP-0083 | 28 | `STELLAR_VALUE_EMPTY_TX_SET` |
| CAP-0085 | 28 | `CONTRACT_EXECUTABLE_EXTERNAL_REF`, `SCV_EXECUTABLE_TAG` |
| CAP-0084 | 30 | `SC_ADDRESS_TYPE_MUXED_CONTRACT` |
| CAP-0087 | 30 | ML-DSA cost types |
| CAP-0088 | 30 | Millisecond close times (`STELLAR_VALUE_SIGNED_MS`, …) |

Findings:

- **No machine-readable CAP → XDR mapping exists.** `stellar-protocol` has only the markdown headers. rs-stellar-xdr ships `xdr-definitions-json/xdr.json`, a machine-readable dump of the XDR definitions with file hashes and resolved feature flags, but nothing ties a definition to a CAP. Feature-flag names sometimes carry the CAP number while a change is gated (rs-stellar-xdr#604 names `cap_0084_muxed_contract`, `cap_0087_ml_dsa`), and the flag disappears when the CAP ships. We will build the mapping; it is a small deliverable in its own right (`coverage/caps.json`: CAP, protocol, shapes, entry IDs, gaps).
- **CAP-0073 shows that CAP tags cannot be derived from XDR diffs**: it changed no XDR and still produced the pattern behind two sightings. Tags are assigned by hand per entry and checked against the CAP header for the protocol number.
- **Tag format:** `CAP-0040`, `CAP-0046-11`: the stellar-protocol file name in upper case, which is also how the RFP and the issues write it.
- **Protocol:** a range, `protocol.min` (first protocol where core can emit the shape, from the CAP header) and optional `protocol.max`, plus `ledger.protocolVersion` for the protocol the bytes were actually produced under. One number is not enough: the signed-payload sample is a protocol-19 shape captured at protocol 28, and a target can only decode an entry whose captured protocol its XDR knows (open question 3).

## 5. Round trip and tooling

Source: rs-stellar-xdr v30.0.0 README and CLI, built locally (`cargo install --locked stellar-xdr --version 30.0.0 --features cli`).

- CLI commands: `types`, `guess`, `decode`, `encode`, `compare`, `generate`, `xfile`, `version`. Input formats: `single`, `single-base64`, `stream`, `stream-base64`, `stream-framed`. Output: `json`, `json-formatted`, `text`, `rust-debug`. `stellar-xdr version` prints the crate version, the XDR commit and the enabled features, which is exactly the provenance to record.
- The `curr`/`next` crate features are gone; individual XDR features are Cargo features (currently only `test_feature`).
- **Version matters:** stellar-xdr 27.0.0 fails on a protocol-30 golden ("xdr value invalid") and decodes a protocol-28 ledger. The manifest records the reference version.
- **XDR-JSON** is SEP-51. 64-bit integers are strings, bytes that are not UTF-8 are escaped (`{"text":"\\xff"}` for the memo sample), addresses are strkeys. One documented exception: a signed-payload signer with an empty payload renders as `<INVALID:G...:EMPTY_PAYLOAD>` and cannot be converted back (`src/_xdrjson.rs`). The schema has `expect.roundTrip.xdrJson: false` for such entries.
- **Decision: XDR-JSON is the language for expected values**, addressed by JSON Pointer. It is specified (SEP-51), produced by the reference implementation, and readable. An optional JSON copy of a payload may be stored but is informative; the binary is authoritative.
- **Encoding of corpus files: raw XDR binary, one value per file**, with SHA-256 of the raw bytes. No base64 (it invites whitespace and padding differences), no framing, no compression at rest (git and CI caches compress anyway, and the largest sample is 58 KB). The harness adds framing, batching and zstd when it builds a layout.
- Where the SDKs differ is given by the sightings, not yet by our own measurement: ordering (JS vs Go), native conversion of maps and strings (JS), 64-bit ID parsing (JS, Python, Java), JSON integer forms (rs-stellar-xdr#627), max-length enforcement on the JSON path (Rust). A first divergence matrix is milestone work.

All ten sample payloads round-trip byte-identical through stellar-xdr 30.0.0 (`tools/validate.py`).

## 6. Replay targets

**Galexie data-lake layout.** Source: go-stellar-sdk `support/datastore/schema.go`, `configure.go`, and the public lake.

- Object key: `[<%08X of (MaxUint32 - partitionStart)>--<partitionStart>-<partitionEnd>/]<%08X of (MaxUint32 - fileStart)>--<fileStart>[-<fileEnd>].xdr.<ext>`. The partition directory is omitted when `filesPerPartition` is 1; the `-<fileEnd>` suffix is omitted when a file holds one ledger.
- Each object is a compressed `LedgerCloseMetaBatch`.
- A manifest at `.config.json` in the datastore root: `{"networkPassphrase","version","compression","ledgersPerBatch","batchesPerPartition"}`.
- Verified against the public lake: `https://aws-public-blockchain.s3.us-east-2.amazonaws.com/v1.1/stellar/ledgers/testnet/2025-12-18/.config.json` returns `{"networkPassphrase":"Test SDF Network ; September 2015","version":"1.0","compression":"zstd","ledgersPerBatch":1,"batchesPerPartition":64000}`, and ledger 4437357 is at `FFBC9DFF--4416000-4479999/FFBC4A92--4437357.xdr.zst`. Testnet has one prefix per reset (`2025-06-18`, `2025-08-14`, `2025-12-17`, `2025-12-18`).
- go-stellar-sdk has a filesystem datastore (`support/datastore/filesystem.go`), so a directory on disk can stand in for a bucket.

**Stellar RPC `getLedgers`.** Source: go-stellar-sdk `protocols/rpc/get_ledgers.go`.

- Request: `startLedger`, `pagination {cursor, limit}`, `xdrFormat` (`base64` or `json`).
- Response: `ledgers[] {hash, sequence, ledgerCloseTime (string), headerXdr, metadataXdr, headerJson?, metadataJson?}`, `latestLedger`, `latestLedgerCloseTime`, `oldestLedger`, `oldestLedgerCloseTime`, `cursor`. `metadataXdr` is the base64 `LedgerCloseMeta`; `headerXdr` is the `LedgerHeaderHistoryEntry` inside it.

**What the manifest needs:** `ledger.sequence`, `ledger.hash`, `ledger.closeTime`, `ledger.networkPassphrase`, `ledger.protocolVersion`, `ledger.lcmVersion`. All are derivable from the payload; they are in the manifest so the harness can lay out files and answer `getLedgers` without decoding, and because the build cross-checks them against the decoded payload (`tools/validate.py` does this).

**Which first.** wallet-backend accepts both: `--ledger-backend-type` is `rpc` or `datastore` (`cmd/ingest.go`; the default is `datastore` at `e2436eef697f`, while the compose file sets `rpc`). Recommendation, revised after the reproduction in [`repro/`](../repro/README.md): **mock `getLedgers` first**, then the Galexie layout generated from the same entries. wallet-backend's datastore backend is S3-only (`internal/ingest/ledger_backend.go` sets `Type: "S3"`), so a local directory is not accepted and a Galexie replay needs an S3-compatible endpoint. The mock is about a hundred lines of standard-library Python and was called twice per run (`getHealth`, `getLedgers`). The Galexie layout stays in scope because it is static and is what the RFP names first; which input stellar-etl needs is still open (question 2).

## 7. Generating real entries offline

**Network capture.** Done once, with network access, by the generator; CI only reads the committed bytes. Verified end to end: the ledger named in wallet-backend#729 (testnet 4437357) was fetched from the public lake, and transaction `d9f78749…` in it is a `SetOptions` adding signer `PBCK2RYY…AANMS`; the operation meta shows the signer stored on the account. That is sample `cap0040-signed-payload-signer-added`. No keys, funds or account are needed to read the lake.

**stellar-core's `--check-lcm` goldens.** Source: stellar-core `docs/software/commands.md`, `src/test/test.cpp`, PR #5435.

- `stellar-core test --rng-seed 12345 '[tx]' --capture-lcm` writes `test-lcm-current/<TestFile>/<16-hex>.xdr` (and `test-lcm-next/` for vnext builds) at the repository root; `--check-lcm <DIR>` compares in CI.
- Each file is a stream of framed `LedgerCloseMeta`. `index.json` maps the hash-named file to `test case|section|…` and is stamped with `!cfg protocol version` (30), `!rng seed` (12345), `!versions to test`.
- Network passphrase is `(V) (;,,;) (V)` for all captured meta. Non-deterministic diagnostic events are zeroed.
- At `f2b9194`: 10 test files and 876 files per tier (691 under `InvokeHostFunctionTests`). Licence Apache-2.0.
- Goldens exist only for the current protocol, and tests that inject entries directly or run multi-node simulations are skipped.
- We scanned `InvokeHostFunctionTests`, `TxEnvelopeTests`, `TxResultsTests` and `MergeTests`: SAC `trust()` appears in 3 ledgers (one is sample `cap0073-sac-trust-creates-trustline`, record 43 of `4c437318f87437c5.xdr`, section "Trustline stellar asset contract|protocol version 30"); **no** ledger has a muxed source account or a signed-payload signer. So the goldens cover Soroban well and the classic rare shapes poorly. The muxed-source entry was therefore generated on a local core (`generator/muxed_soroban_source.mjs`).
- Proposed meaning of "agree with the goldens": (a) every golden at a pinned core commit decodes and round-trips through the corpus tooling; (b) an entry copied from a golden records repository, commit, path, record and file hash, and CI re-verifies the bytes; (c) an entry from another source that has the same shape as a golden says so (`relation: same-shape`). Open question 1.

**Local core.** Source: stellar/quickstart README. `docker run stellar/quickstart --local` runs core, Horizon, RPC and Galexie; ledger meta is served at `/ledger-meta` with `--local` and `--enable galexie`; `--protocol-version` and `--limits` are options; passphrase `Standalone Network ; February 2017`; one ledger per second. The root account of a local network is derived from the passphrase, so no external keys or funds are involved.

- A quickstart run is **not byte-reproducible**: close times come from the wall clock and the ledger in which a transaction lands varies. What is reproducible is the shape. So the bytes are committed, the recipe and the core version or image digest are recorded, and regeneration is checked by running the entry's own `expect` assertions against the new bytes.
- "Reproducible for both testnet and mainnet passphrases": run the local core with `NETWORK_PASSPHRASE` set to either public passphrase. Whether quickstart exposes this or we drive stellar-core directly is open question 5.
- Byte-reproducible alternative: stellar-core's own test harness with a fixed seed, as the goldens do. It needs a core build with tests and uses the test passphrase.

**Synthetic entries.** Built with `stellar-xdr encode` from XDR-JSON, or written as bytes. Always `synthetic: true`, with `method`, optional `derivedFrom`, and `coreCanEmit` (`yes`, `no`, `unverified`) plus the evidence. Entries with `coreCanEmit: no` stay out of the replay harness by default; they exist for SDK conformance.

## 8. Prior art

Inside Stellar:

- **stellar-core goldens:** hash-named files plus an `index.json` stamped with the configuration that produced them. We keep the idea of stamping provenance and of a generated index.
- **wallet-backend:** `internal/indexer/testdata/ledger-<seq>.xdr.gz` and `processors/testdata/testnet_<seq>_<txhash prefix>_{envelope,meta}.b64`. No manifest; the meaning lives in the Go tests.
- **js-stellar-sdk:** `test/fixtures/{horizon-corpus,rpc-corpus,legacy-xdr}` refreshed by scripts; each file is `{source, fetchedAt, records}`. We reuse `source`/`fetchedAt` as `retrievedFrom`/`retrievedAt`.
- **rs-stellar-xdr:** inline vectors in `src/tests/*.rs` and a fuzz corpus; no shared format.
- None of these is language-neutral with expected values, which is the gap the RFP names.

Outside Stellar (from memory, not re-read today; treat as design inspiration only):

- **Wycheproof:** each vector has an ID, a comment, flags naming the weakness, and a result of valid, invalid or acceptable. We take flags (`rootCauses`) and the idea that more than one outcome can be acceptable (`expect.operations[].outcomes`).
- **Ethereum execution-spec-tests:** fixtures carry an info block (hash, tool version that filled them, reference spec, fork) and the release ships an index of all fixtures. We take the provenance block, the fork tag (our protocol range) and the generated index.
- **Protobuf conformance suite:** a runner drives each implementation and accepts "skipped"; per-implementation expected-failure lists live with the runner. We take `skipped` as a verdict and keep known divergences in runner configuration, not in the manifest.

## Open questions

1. **Meaning of "agree with --check-lcm goldens".** Our reading is in section 7. To confirm with the SCF/SDF contact, since goldens use a test passphrase and only the current protocol.
2. **Non-contiguous ledgers.** Entries have unrelated sequence numbers (4437357, 44) and different passphrases. Proposed: the harness serves each entry as its own one-ledger datastore or RPC range and starts the target once per entry (or per group), which also isolates verdicts and bounds memory. To validate against wallet-backend's backfill and live paths.
3. **Captured protocol vs target XDR.** The CAP-0073 golden is protocol 30 and its header uses the `SIGNED_MS` arm; wallet-backend's pre-fix commit for #731 (go-stellar-sdk v0.7.3) does not know that arm and cannot decode it (measured; it does decode a protocol-30 ledger with the older `SIGNED` arm). Reproducing a sighting needs the shape captured at a protocol the pre-fix commit understands (local core with `--protocol-version`), or a dependency bump on the pre-fix commit, which weakens "pre-fix". Do we keep several captures of one shape (one entry per protocol) or one entry with several payloads? Draft answer: separate entries, IDs suffixed `-p28`. **Update after the reproduction:** an older recording of the same golden (stellar-core `09f7d11b5f1d`) holds the same ledger with the `SIGNED` header arm, which the pre-fix commit decodes, and it reproduces #731 ([`repro/`](../repro/README.md)). The general point stands: an entry's captured XDR version decides which pre-fix commits it can be replayed into, so the generator must be able to produce a shape at more than one protocol/XDR version.
4. ~~Does wallet-backend's `datastore` backend accept a filesystem datastore?~~ **Answered: no.** It is S3-only, so the first replay input is the mock `getLedgers` server, which reproduced wallet-backend#729 ([`repro/`](../repro/README.md)). Still open: live mode on an empty database loads a history-archive checkpoint; the reproduction avoids it by seeding the ingest cursor. Whether the harness should instead serve a minimal local archive is to be decided.
5. **Passphrase on a local core:** does quickstart allow overriding the passphrase, or do we ship our own minimal stellar-core configuration?
6. **Pre-fix/fixed commit pair for wallet-backend#716.** The issue was closed without a linked PR and has three producers fixed by different PRs (#729, #745, #693/#753). Two producers are reproduced with their own pairs ([`repro/`](../repro/README.md)); the SEP-41 producer is open.
7. **Field-by-field `coreValidation`.** Not yet read from stellar-core source. Draft entries use `to-verify` where unsure. Needs the core-engineer review the RFP already requires for the guide.
8. **Definition of "participant".** Draft: every address whose ledger entry the focus transaction's meta shows created, updated, removed or restored (including fee changes), plus addresses named in its events. Is the native asset contract that emits the fee event a participant? Draft answer: no.
9. **Shapes that predate CAPs** (memo, protocol 1). The RFP says every entry is tagged with a CAP. Draft: `caps: []` is allowed only with `protocol.min` below 10.
10. **Licence of captured network data.** Ledger data is public; we treat entries as Apache-2.0 like the rest of the corpus and record the source URL. Goldens copied from stellar-core are Apache-2.0. To confirm no objection from SDF.
11. **Non-XDR defects** (js-xdr#165, half of js-stellar-sdk#1785). They cannot be corpus entries. Draft: operation checks attached to the nearest value entry; pure encoder-input checks stay in the conformance runner's own test list.
12. **henyey#3941** (bucket files). Out of first scope: the cause is not isolated upstream and bucket files are large. The schema can carry a `BucketEntry` value later.
13. **Schema `$id`.** Currently a URN placeholder; to become the URL of the schema file in the public repository.
