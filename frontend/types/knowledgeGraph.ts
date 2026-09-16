// KnowEvo ontology workbench types (T-05b). Mirrors the frozen
// /api/knowevo/ontology endpoints in backend/apps/knowledge_graph_app.py.

export interface OntologyEvidenceSpan {
  doc?: string;
  text?: string;
  [key: string]: unknown;
}

export interface OntologyProposal {
  id: string;
  round_id: string;
  target: string;
  op: string;
  payload: {
    name?: string;
    parent?: string | null;
    evidence_spans?: OntologyEvidenceSpan[];
    [key: string]: unknown;
  };
  confidence: number | null;
  impact: number | null;
  novelty: number | null;
  status: string;
  trigger_source: string;
  score: number;
  ev_rich: number;
}

export interface OntologyQueuePage {
  items: OntologyProposal[];
  total: number;
  page: number;
  page_size: number;
}

export type OntologyReviewAction = "confirm" | "reject" | "reparent";

export interface OntologyReviewResult {
  action: OntologyReviewAction;
  updated: number;
  new_parent: string | null;
  status: string;
}

export interface OntologyVersionRow {
  version: string;
  status: string;
  snapshot: {
    classes: OntologyClassNode[];
    rel_types: unknown[];
  };
  applied_ops: unknown[];
  metrics?: OntologyMetrics | null;
  folded?: number;
}

export interface OntologyClassNode {
  name: string;
  parent?: string | null;
  stable_id?: string;
  anchor?: boolean;
  deprecated?: boolean;
  props?: { name: string; type?: string }[];
  [key: string]: unknown;
}

export interface OntologyMetrics {
  cov: number;
  red: number;
  dep: number;
  align: number;
}

export interface OntologyDiffOp {
  op: string;
  target: string;
  payload: Record<string, unknown>;
}

export interface OntologyDiffResult {
  from: string;
  to: string;
  ops: OntologyDiffOp[];
}
