#!/usr/bin/env node
// Run a value corpus entry against an installed @stellar/stellar-sdk and report
// a verdict per check: byte round trip, then each expect.operations item.
//
//   node run_js_sdk.mjs SDK_DIR ENTRY_DIR
//
// SDK_DIR is a directory with node_modules/@stellar/stellar-sdk installed.
// Offline; reads only the entry and the installed package.
import { createRequire } from "node:module";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const [sdkDir, entryDir] = process.argv.slice(2).map((p) => resolve(p));
const require = createRequire(join(sdkDir, "package.json"));
const sdk = require("@stellar/stellar-sdk");
const version = JSON.parse(
  readFileSync(join(sdkDir, "node_modules/@stellar/stellar-sdk/package.json"), "utf8"),
).version;

const entry = JSON.parse(readFileSync(join(entryDir, "entry.json"), "utf8"));
const payload = entry.payloads.find((p) => p.role === "primary");
const raw = readFileSync(join(entryDir, payload.file));
if (createHash("sha256").update(raw).digest("hex") !== payload.sha256) {
  throw new Error("payload digest does not match the manifest");
}

const checks = [];
const value = sdk.xdr[payload.xdrType].fromXDR(raw);
checks.push({
  check: "round-trip",
  verdict: Buffer.compare(value.toXDR(), raw) === 0 ? "pass" : "diverged",
});

// Number of entries a native map value holds, whatever shape the SDK returns.
function entryCount(native) {
  if (native instanceof Map) return native.size;
  if (Array.isArray(native)) return native.length;
  return Object.keys(native).length;
}

// The pool-share parameters of a decoded ChangeTrustAsset, as SDK Asset objects
// in the payload's own order.
function poolParams(v) {
  const p = v.liquidityPool.constantProduct;
  return {
    assetA: sdk.Asset.fromOperation(p.assetA),
    assetB: sdk.Asset.fromOperation(p.assetB),
    fee: p.fee,
  };
}

// Each operation has `prepare` (runner code that reads the decoded payload; a
// failure there is a runner error, never a verdict) and `run` (the SDK call
// under test; a throw there is the SDK's "error" outcome). `run` returns a
// native value, or { xdr } when its result is XDR bytes.
const OPS = {
  "scval-to-native": { prepare: (v) => v, run: (v) => sdk.scValToNative(v) },
  // Hand the map's entries to the SDK's sorting helper in reverse order.
  "scval-map-sort": {
    prepare: (v) => [...v.map].reverse(),
    run: (entries) => ({ xdr: sdk.scvSortedMap(entries).toXDR() }),
  },
  "liquidity-pool-build": {
    prepare: poolParams,
    run: ({ assetA, assetB, fee }) => ({
      xdr: new sdk.LiquidityPoolAsset(assetA, assetB, fee).toXDRObject().toXDR(),
    }),
  },
  // Read a string-typed union arm through the SDK's native-string getter and
  // build a new value of the same arm from that string.
  "rebuild-from-native-string": {
    prepare: (v) => {
      if (v.type !== "memoText") throw new Error(`runner: rebuild-from-native-string has no rule for ${v.type}`);
      return v;
    },
    run: (v) => ({ xdr: sdk.xdr.Memo.memoText(v.value).toXDR() }),
  },
  // Rebuild the payload's ManageBuyOffer with another offer ID.
  "offer-id-from-decimal-string": {
    prepare: (v, args) => ({ ...sdk.Operation.fromXDRObject(v), offerId: args.offerId }),
    run: (opts) => ({ xdr: sdk.Operation.manageBuyOffer(opts).toXDR() }),
  },
  // Same, with the ID converted to the language's floating-point number first.
  "offer-id-from-float": {
    prepare: (v, args) => ({ ...sdk.Operation.fromXDRObject(v), offerId: Number(args.offerId) }),
    run: (opts) => ({ xdr: sdk.Operation.manageBuyOffer(opts).toXDR() }),
  },
  // Build a muxed account from the payload's base key and an ID string.
  "muxed-id-parse": {
    prepare: (v, args) => ({
      base: new sdk.Account(sdk.StrKey.encodeEd25519PublicKey(Buffer.from(v.med25519.ed25519.toBytes())), "0"),
      id: args.id,
    }),
    run: ({ base, id }) => {
      const m = new sdk.MuxedAccount(base, id);
      return { xdr: m.toXDRObject().toXDR(), idString: m.id() };
    },
  },
  "liquidity-pool-id": {
    prepare: poolParams,
    run: (params) => Buffer.from(sdk.getLiquidityPoolId("constant_product", params)).toString("hex"),
  },
};
const INVARIANTS = {
  "entry-count": (o) => entryCount(o.value),
  "id-string": (o) => o.idString,
};

for (const op of entry.expect.operations ?? []) {
  const impl = OPS[op.op];
  if (!impl) {
    checks.push({ check: op.op, verdict: "skipped" });
    continue;
  }
  const input = impl.prepare(value, op.args ?? {});
  let observed;
  try {
    const native = impl.run(input);
    observed =
      native && typeof native === "object" && "xdr" in native
        ? { xdrHex: Buffer.from(native.xdr).toString("hex"), ...("idString" in native ? { idString: native.idString } : {}) }
        : { value: native, shape: native instanceof Map ? "Map" : typeof native };
  } catch (e) {
    observed = { error: String(e.message ?? e) };
  }
  const ok = op.outcomes.some((o) => {
    if (o.error) return "error" in observed;
    if ("xdrHex" in o) return observed.xdrHex === o.xdrHex;
    if ("value" in o) return "value" in observed && JSON.stringify(observed.value) === JSON.stringify(o.value);
    if (o.invariant && !("error" in observed)) {
      return INVARIANTS[o.invariant.name](observed) === o.invariant.equals;
    }
    return false;
  });
  const shown =
    "error" in observed || "xdrHex" in observed
      ? observed
      : typeof observed.value !== "object" || observed.value === null
        ? { value: observed.value }
      : {
          shape: observed.shape,
          entryCount: entryCount(observed.value),
          value: observed.value instanceof Map ? [...observed.value.entries()] : observed.value,
        };
  checks.push({ check: op.op, ...(op.args ? { args: op.args } : {}), verdict: ok ? "pass" : "diverged", observed: shown });
}

const verdict = checks.some((c) => c.verdict === "diverged") ? "diverged" : "pass";
console.log(
  JSON.stringify(
    { entry: entry.id, sdk: "@stellar/stellar-sdk", version, node: process.version, verdict, checks },
    (_, v) => (typeof v === "bigint" ? v.toString() : v),
    2,
  ),
);
