import assert from "node:assert/strict";
import test from "node:test";

import {
  evidenceSourceDisplay,
  // @ts-ignore -- Node's built-in TypeScript runner needs the extension.
} from "../types/decisionCard.ts";

// The evidence row's source line once rendered as the literal `None · None`:
// the backend stringified JSON nulls and the panel joined the fields
// blindly. evidenceSourceDisplay is the panel's last line of defence, so it
// must degrade dirty wire values to labels ("图谱内证据" / "—") and never
// let a placeholder reach the screen as text.

test("doc and span both resolve to a joined fields display", () => {
  const source = evidenceSourceDisplay(
    { doc: "指南2024版", span: "chunk 3", kg_path: [], version_pinned: true },
    "kg"
  );

  assert.deepEqual(source, {
    kind: "fields",
    doc: "指南2024版",
    span: "chunk 3",
  });
});

test("a lone doc or span still renders (no dangling separator source)", () => {
  assert.deepEqual(
    evidenceSourceDisplay(
      { doc: "指南2024版", span: "", kg_path: [], version_pinned: false },
      "doc"
    ),
    { kind: "fields", doc: "指南2024版", span: "" }
  );
  assert.deepEqual(
    evidenceSourceDisplay(
      { doc: "", span: "§9.2", kg_path: [], version_pinned: false },
      "doc"
    ),
    { kind: "fields", doc: "", span: "§9.2" }
  );
});

test("kg evidence with no resolvable source degrades to the graph label", () => {
  for (const channel of ["kg", "kg+doc"]) {
    const source = evidenceSourceDisplay(
      { doc: "", span: "", kg_path: ["a -> b"], version_pinned: true },
      channel
    );
    assert.deepEqual(source, { kind: "graph" }, `channel=${channel}`);
  }
});

test("a non-kg row with no source is honestly missing", () => {
  assert.deepEqual(
    evidenceSourceDisplay(
      { doc: "", span: "", kg_path: [], version_pinned: false },
      "doc"
    ),
    { kind: "missing" }
  );
});

test('literal "None"/"null" placeholders and JSON nulls count as missing', () => {
  const dirty = evidenceSourceDisplay(
    // The wire types these as strings, but live payloads carry nulls and
    // model-echoed placeholders - exactly what printed `None · None`.
    { doc: "None", span: null, kg_path: [], version_pinned: true } as never,
    "kg"
  );
  assert.deepEqual(dirty, { kind: "graph" });

  assert.deepEqual(
    evidenceSourceDisplay(
      { doc: "null", span: "  ", kg_path: [], version_pinned: false } as never,
      "doc"
    ),
    { kind: "missing" }
  );
  assert.deepEqual(evidenceSourceDisplay(undefined, "kg"), {
    kind: "graph",
  });
});
