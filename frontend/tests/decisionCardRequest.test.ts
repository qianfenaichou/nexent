import assert from "node:assert/strict";
import test from "node:test";

import {
  buildDecisionCardRequest,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "../types/decisionCard.ts";

// T-25 pins the POST /knowevo/decision/card body builder. The contract is:
//  - as_of (business/fact time) and ontology_version are separate optional
//    pins, each carried only when non-empty;
//  - with neither set the body must stay key-for-key identical to the
//    pre-T-25 payload (question, mode) so existing callers are unaffected.

test("default request keeps the pre-T-25 body: question + mode only", () => {
  const body = buildDecisionCardRequest({ question: "eGFR 45?", mode: "full" });

  assert.deepEqual(body, { question: "eGFR 45?", mode: "full" });
  assert.deepEqual(Object.keys(body), ["question", "mode"]);
});

test("mode defaults to full when omitted", () => {
  const body = buildDecisionCardRequest({ question: "q" });

  assert.deepEqual(body, { question: "q", mode: "full" });
});

test("as_of alone is carried without ontology_version", () => {
  const body = buildDecisionCardRequest({
    question: "q",
    asOf: "2020-01-01T00:00",
    mode: "full",
  });

  assert.deepEqual(body, {
    question: "q",
    mode: "full",
    as_of: "2020-01-01T00:00",
  });
  assert.deepEqual(Object.keys(body), ["question", "mode", "as_of"]);
  assert.equal("ontology_version" in body, false);
});

test("version + as_of are both carried (the T-23 version-compare recipe)", () => {
  const body = buildDecisionCardRequest({
    question: "q",
    ontologyVersion: "v1.0.0",
    asOf: "2024-01-01T00:00",
    mode: "lite",
  });

  assert.deepEqual(body, {
    question: "q",
    mode: "lite",
    ontology_version: "v1.0.0",
    as_of: "2024-01-01T00:00",
  });
  assert.deepEqual(Object.keys(body), [
    "question",
    "mode",
    "ontology_version",
    "as_of",
  ]);
});

test("clearing as_of (empty string) drops the key and restores the old body", () => {
  const body = buildDecisionCardRequest({
    question: "q",
    ontologyVersion: "",
    asOf: "",
    mode: "full",
  });

  assert.deepEqual(body, { question: "q", mode: "full" });
  assert.equal("as_of" in body, false);
  assert.equal("ontology_version" in body, false);
  assert.deepEqual(Object.keys(body), ["question", "mode"]);
});
