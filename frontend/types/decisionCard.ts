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
