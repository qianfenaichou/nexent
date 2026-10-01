// Unit tests for load-failure classification (honest empty vs pending-wiring).
// After W5/α lands: list 404 = still unwired; single-round 404 = not_found.
import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyLoadFailure,
  isPendingWiring,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "./errorClass.ts";

class FakeApiError extends Error {
  code: string | number;

  constructor(code: string | number, message = "api") {
    super(message);
    this.code = code;
  }
}

test("timeline 404/405 means pending_wiring (wired list never 404s)", () => {
  assert.equal(
    classifyLoadFailure(new FakeApiError(404), "timeline"),
    "pending_wiring"
  );
  assert.equal(
    classifyLoadFailure(new FakeApiError("405"), "timeline"),
    "pending_wiring"
  );
  assert.equal(
    isPendingWiring(classifyLoadFailure(new FakeApiError(404), "timeline")),
    true
  );
});

test("round 404 is not_found after wiring, never pending_wiring", () => {
  assert.equal(
    classifyLoadFailure(new FakeApiError(404), "round"),
    "not_found"
  );
  assert.equal(
    isPendingWiring(classifyLoadFailure(new FakeApiError(404), "round")),
    false
  );
});

test("403 is forbidden (tenant / RBAC), never pending_wiring", () => {
  assert.equal(classifyLoadFailure(new FakeApiError(403)), "forbidden");
  assert.equal(
    classifyLoadFailure(new FakeApiError(403), "round"),
    "forbidden"
  );
});

test("other 4xx is not_found; 5xx is server", () => {
  assert.equal(classifyLoadFailure(new FakeApiError(400)), "not_found");
  assert.equal(classifyLoadFailure(new FakeApiError(422)), "not_found");
  assert.equal(classifyLoadFailure(new FakeApiError(502)), "server");
  assert.equal(classifyLoadFailure(new FakeApiError(500)), "server");
});

test("no status code is network (fetch TypeError etc.)", () => {
  assert.equal(
    classifyLoadFailure(new TypeError("Failed to fetch")),
    "network"
  );
  assert.equal(classifyLoadFailure(undefined), "network");
  assert.equal(classifyLoadFailure("boom"), "network");
});
