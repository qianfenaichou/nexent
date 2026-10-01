// Unit tests for skill-gallery apply fallback classification.
// Honesty contract: every non-OK path is client_preview + a reason.
// After W10/α lands: HTTP 404 = template not found (not "route pending").
import assert from "node:assert/strict";
import test from "node:test";

import {
  classifyApplyFallback,
  classifyApplyStatus,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "./errorClass.ts";

class FakeApiError extends Error {
  code: string | number;

  constructor(code: string | number, message = "api") {
    super(message);
    this.code = code;
  }
}

test("404 after W10 is template_missing, not route_pending", () => {
  assert.equal(classifyApplyStatus(404), "template_missing");
  assert.equal(
    classifyApplyFallback(new FakeApiError(404)),
    "template_missing"
  );
});

test("405 still means route/method not available", () => {
  assert.equal(classifyApplyStatus(405), "route_pending");
  assert.equal(classifyApplyFallback(new FakeApiError(405)), "route_pending");
});

test("403 is forbidden; other 4xx is template_missing; 5xx is server", () => {
  assert.equal(classifyApplyStatus(403), "forbidden");
  assert.equal(classifyApplyStatus(400), "template_missing");
  assert.equal(classifyApplyStatus(422), "template_missing");
  assert.equal(classifyApplyStatus(502), "server");
  assert.equal(classifyApplyFallback(new FakeApiError(502)), "server");
});

test("network failures have no status", () => {
  assert.equal(
    classifyApplyFallback(new TypeError("Failed to fetch")),
    "network"
  );
  assert.equal(classifyApplyFallback(undefined), "network");
});
