#!/usr/bin/env node
// Generator recipe: a ledger in which a successful ExtendFootprintTTL operation
// has a muxed (M…) operation source.
//
//   node muxed_soroban_source.mjs SDK_DIR RPC_URL OUT_DIR
//
// Target: a local stellar-core network (stellar/quickstart --local). Uses no
// external keys or funds: the funding account is the network root account,
// derived from the passphrase, and the test account's seed is a fixed hash.
// Writes OUT_DIR/ledger.xdr (one LedgerCloseMeta) and OUT_DIR/recipe-run.json.
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { mkdirSync, writeFileSync } from "node:fs";
import { join, resolve } from "node:path";

const [sdkDir, rpcUrl, outDir] = process.argv.slice(2);
const require = createRequire(join(resolve(sdkDir), "package.json"));
const sdk = require("@stellar/stellar-sdk");
const { Keypair, Asset, Operation, TransactionBuilder, MuxedAccount, Account } = sdk;

const MUX_ID = "1234567890123456789"; // above 2^53 on purpose
const server = new sdk.rpc.Server(rpcUrl, { allowHttp: true });
const { passphrase, protocolVersion } = await server.getNetwork();
const root = Keypair.master(passphrase);
const user = Keypair.fromRawEd25519Seed(
  createHash("sha256").update("xdr-corpus muxed-soroban-source").digest(),
);

async function submit(tx, signer, label) {
  tx.sign(signer);
  const sent = await server.sendTransaction(tx);
  if (sent.status !== "PENDING") throw new Error(`${label}: send ${sent.status} ${JSON.stringify(sent.errorResult ?? "")}`);
  for (let i = 0; i < 60; i++) {
    const got = await server.getTransaction(sent.hash);
    if (got.status === "SUCCESS") return { hash: sent.hash, ledger: got.ledger };
    if (got.status === "FAILED") throw new Error(`${label}: FAILED ${got.resultXdr?.toXDR?.("base64") ?? ""}`);
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error(`${label}: timed out`);
}
const builder = async (pub) =>
  new TransactionBuilder(await server.getAccount(pub), { fee: "1000000", networkPassphrase: passphrase });

// Soroban resources are set by hand, generously, instead of through
// simulateTransaction: the quickstart RPC (v30) fails to simulate on a network
// held at an older protocol version. Unused resource fee is refunded.
const resources = ({ readOnly = [], readWrite = [], instructions = 0, writeBytes = 0 }) =>
  new sdk.SorobanDataBuilder()
    .setFootprint(readOnly, readWrite)
    .setResources(instructions, 2000, writeBytes)
    .setResourceFee("50000000")
    .build();

// 1. Fund the test account from the root account.
//    Retried: right after start-up the network may still be upgrading and the
//    root account may have a transaction of quickstart's own in the queue.
let funded;
for (let attempt = 1; funded === undefined; attempt++) {
  try {
    await server.getAccount(user.publicKey());
    funded = funded ?? null; // already funded by an earlier run
    break;
  } catch {}
  try {
    const tx = (await builder(root.publicKey()))
      .addOperation(Operation.createAccount({ destination: user.publicKey(), startingBalance: "1000" }))
      .setTimeout(30)
      .build();
    funded = await submit(tx, root, "createAccount");
  } catch (e) {
    if (attempt === 10) throw e;
    console.error(`createAccount attempt ${attempt}: ${e.message}; retrying`);
    await new Promise((r) => setTimeout(r, 5000));
  }
}

// 2. Make sure the native asset contract exists; its instance entry is what the TTL extension targets.
const contractId = Asset.native().contractId(passphrase);
const instanceKey = new sdk.Contract(contractId).getFootprint();
let deployed;
if ((await server.getLedgerEntries(instanceKey)).entries.length === 0) {
  const tx = (await builder(user.publicKey()))
    .addOperation(Operation.createStellarAssetContract({ asset: Asset.native() }))
    .setSorobanData(resources({ readWrite: [instanceKey], instructions: 3_000_000, writeBytes: 2000 }))
    .setTimeout(60)
    .build();
  deployed = await submit(tx, user, "deploy native asset contract");
}

// 3. ExtendFootprintTTL with a muxed operation source. The transaction source is
//    the plain G… account; only the operation names the M… form.
//    The SDK refuses to build a Soroban operation with a muxed source, so the
//    operation is built without a source and the source is set on the envelope
//    before signing.
const muxedAccount = new MuxedAccount(new Account(user.publicKey(), "0"), MUX_ID);
const muxed = muxedAccount.accountId();
const plain = (await builder(user.publicKey()))
  .addOperation(Operation.extendFootprintTtl({ extendTo: 200000 }))
  .setSorobanData(resources({ readOnly: [instanceKey] }))
  .setTimeout(60)
  .build();
const envelope = plain.toEnvelope();
envelope.v1.tx.operations[0].sourceAccount = muxedAccount.toXDRObject();
const tx = new sdk.Transaction(Buffer.from(envelope.toXDR()).toString("base64"), passphrase);
const done = await submit(tx, user, "extendFootprintTtl");

// 4. Fetch that ledger's LedgerCloseMeta as raw base64 through JSON-RPC.
const res = await fetch(rpcUrl, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "getLedgers", params: { startLedger: done.ledger, pagination: { limit: 1 } } }),
});
const ledger = (await res.json()).result.ledgers[0];
const raw = Buffer.from(ledger.metadataXdr, "base64");
mkdirSync(outDir, { recursive: true });
writeFileSync(join(outDir, "ledger.xdr"), raw);
const run = {
  passphrase,
  protocolVersion,
  account: user.publicKey(),
  muxedSource: muxed,
  muxId: MUX_ID,
  contractId,
  funded,
  deployed,
  extend: done,
  ledger: { sequence: ledger.sequence, hash: ledger.hash, closeTime: ledger.ledgerCloseTime },
  payload: { bytes: raw.length, sha256: createHash("sha256").update(raw).digest("hex") },
};
writeFileSync(join(outDir, "recipe-run.json"), JSON.stringify(run, null, 2) + "\n");
console.log(JSON.stringify(run, null, 2));
