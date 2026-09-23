// KnowEvo decision-card types (T-19). Mirrors the wire contract
// (services/knowevo/schemas.py DecisionCardContract, extra="allow") as
// served by POST /api/knowevo/decision/card and the decision_card_render
// MCP tool - one card shape for the panel and the chat renderer.

export interface DecisionCardProvenance {
  doc: string;
  span: string;
  kg_path: string[];
  version_pinned: boolean;
}

/**
 * What the evidence row's source line may show.
 *
 * - `fields`: a resolvable source; `doc` and/or `span` are cleaned non-empty
 *   strings to join with " · ".
 * - `graph`: kg-channel evidence whose document source never resolved -
 *   still real graph evidence, shown under a label like "图谱内证据".
 * - `missing`: nothing known about the source - shown as an em dash, never
 *   as fabricated text.
 */
export type EvidenceSourceDisplay =
  | { kind: "fields"; doc: string; span: string }
  | { kind: "graph" }
  | { kind: "missing" };

/**
 * Normalize one evidence row's provenance into what the panel may render.
 *
 * The wire contract types doc/span as strings, but live payloads carry JSON
 * nulls and models echo literal "None"/"null" placeholders for sources the
 * run never resolved - all of which must degrade to a fallback label rather
 * than reach the screen as text (the panel once printed `None · None`).
 */
export function evidenceSourceDisplay(
  provenance: DecisionCardProvenance | null | undefined,
  sourceChannel: string
): EvidenceSourceDisplay {
  // Fields are read defensively: at runtime they may be null, undefined or
  // a placeholder despite the declared `string` type.
  const doc = cleanSourceField(provenance?.doc);
  const span = cleanSourceField(provenance?.span);
  if (doc !== "" || span !== "") {
    return { kind: "fields", doc, span };
  }
  return sourceChannel.startsWith("kg")
    ? { kind: "graph" }
    : { kind: "missing" };
}

function cleanSourceField(value: unknown): string {
  if (value === null || value === undefined) return "";
  const text = String(value).trim();
  const lowered = text.toLowerCase();
  return lowered === "none" || lowered === "null" ? "" : text;
}

export interface DecisionCardEvidenceItem {
  claim: string;
  provenance: DecisionCardProvenance;
  tag: string; // EXTRACTED | INFERRED
  source_channel: string; // kg | doc | kg+doc
  contested: boolean;
}

export interface DecisionCardCounterfactual {
  not_choose: string;
  tag: string;
}

export interface DecisionCardCandidate {
  option: string;
  score: number;
  confidence_calibrated: number;
  evidence_chain: DecisionCardEvidenceItem[];
  risks: string[];
  counterfactual: DecisionCardCounterfactual | null;
}

export interface DecisionCardConflictAdjudication {
  conflict_id: string;
  type: string;
  resolution: string;
}

export interface DecisionCardKnowledgeStamp {
  ontology_version: string | null;
  kg_cutoff: string | null;
  clock_source: string;
}

export interface DecisionCardPayload {
  question_id: string;
  question: string;
  knowledge_stamp: DecisionCardKnowledgeStamp;
  candidates: DecisionCardCandidate[];
  decision: string; // RECOMMEND | INSUFFICIENT_EVIDENCE
  conflict_adjudications: DecisionCardConflictAdjudication[];
  uncertainty_notes: string[];
  disclaimer: string;
  route: string;
  calibration_applied: boolean;
  knowledge_version_pinned: boolean;
  used_tokens: number;
  elapsed_ms: number;
  // Exposure-layer extras (the contract is extra="allow")
  persisted?: boolean;
  card_id?: string;
}

export type DecisionCardMode = "full" | "lite";

// T-25: the request body is part of the same wire contract, so the builder
// lives here. This module stays dependency-free (no `@/` imports), which lets
// node:test import and exercise the builder without the Next runtime.
export interface DecisionCardRequestInput {
  question: string;
  ontologyVersion?: string;
  asOf?: string;
  mode?: DecisionCardMode;
}

/**
 * Build the `POST /api/knowevo/decision/card` request body.
 *
 * `as_of` (business/fact time) and `ontology_version` are optional pins and
 * are only carried when non-empty. Key order and the default `mode` match the
 * pre-T-25 payload exactly, so omitting `asOf` yields a body that is key-for-key
 * identical to the old one (asserted in tests/decisionCardRequest.test.ts).
 */
export function buildDecisionCardRequest(
  input: DecisionCardRequestInput
): Record<string, unknown> {
  const body: Record<string, unknown> = {
    question: input.question,
    mode: input.mode ?? "full",
  };
  if (input.ontologyVersion) {
    body.ontology_version = input.ontologyVersion;
  }
  if (input.asOf) {
    body.as_of = input.asOf;
  }
  return body;
}
