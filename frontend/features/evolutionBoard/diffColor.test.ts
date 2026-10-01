// Unit tests for three-color op painting (node:test, run via
// `node --test features/evolutionBoard/diffColor.test.ts`).
import assert from "node:assert/strict";
import test from "node:test";

import {
  opColorKey,
  paintOp,
  toDiffOpView,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "./diffColor.ts";

test("ADD family paints green / added", () => {
  assert.equal(opColorKey("CLS_ADD"), "added");
  assert.equal(opColorKey("ADD"), "added");
  assert.equal(paintOp("REL_ADD").color, "green");
  assert.equal(paintOp("REL_ADD").label, "added");
});

test("DEPRECATE and DEL paint grey / deprecated", () => {
  assert.equal(opColorKey("CLS_DEPRECATE"), "deprecated");
  assert.equal(opColorKey("REL_DEL"), "deprecated");
  assert.equal(paintOp("CLS_DEL").color, "default");
  assert.equal(paintOp("CLS_DEL").label, "deprecated");
});

test("UPD paints gold / changed", () => {
  assert.equal(opColorKey("PROP_UPD"), "changed");
  assert.equal(paintOp("UPD").color, "gold");
  assert.equal(paintOp("UPD").label, "changed");
});

test("unknown ops fall back to other without inventing a colour story", () => {
  assert.equal(opColorKey("WEIRD_OP"), "other");
  assert.equal(paintOp("WEIRD_OP").label, "op");
});

test("toDiffOpView keeps payload and never drops the op name", () => {
  const view = toDiffOpView({
    op: "CLS_ADD",
    target: "Drug",
    payload: { parent: "Entity" },
  });
  assert.equal(view.op, "CLS_ADD");
  assert.equal(view.target, "Drug");
  assert.deepEqual(view.payload, { parent: "Entity" });
  assert.equal(view.colorKey, "added");
});

test("toDiffOpView tolerates a missing payload", () => {
  const view = toDiffOpView({ op: "CLS_UPD", target: "Drug" });
  assert.deepEqual(view.payload, {});
  assert.equal(view.colorKey, "changed");
});
