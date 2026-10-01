// Unit tests for ops_summary projection (reserved keys vs op counts).
import assert from "node:assert/strict";
import test from "node:test";

import {
  isReservedOpsKey,
  opCounts,
  opsStatus,
  timelineStatusColor,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "./opsSummary.ts";

test("underscore keys are reserved and never rendered as op counts", () => {
  assert.equal(isReservedOpsKey("_status"), true);
  assert.equal(isReservedOpsKey("_steps"), true);
  assert.equal(isReservedOpsKey("CLS_ADD"), false);
  assert.deepEqual(
    opCounts({ _status: "settled", _steps: [], CLS_ADD: 2, REL_UPD: 5 }),
    // reserved excluded; sorted by count desc then key
    [
      { key: "REL_UPD", count: 5 },
      { key: "CLS_ADD", count: 2 },
    ]
  );
});

test("non-numeric op values are skipped, not coerced to 0", () => {
  assert.deepEqual(opCounts({ A: "x", B: 3, C: null }), [
    { key: "B", count: 3 },
  ]);
});

test("opsStatus reads reserved _status and defaults to running", () => {
  assert.equal(opsStatus({ _status: "rolled_back" }), "rolled_back");
  assert.equal(opsStatus({}), "running");
  assert.equal(opsStatus(null), "running");
});

test("timeline status colours stay stable for the Timeline dots", () => {
  assert.equal(timelineStatusColor("settled"), "green");
  assert.equal(timelineStatusColor("failed"), "red");
  assert.equal(timelineStatusColor("rolled_back"), "gray");
  assert.equal(timelineStatusColor("running"), "blue");
});
