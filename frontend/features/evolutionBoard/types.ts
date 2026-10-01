// KnowEvo evolution-board types (L10). Mirrors EvolutionService.RoundSummary /
// RoundReport / StepRecord (backend/services/knowevo/evolution_service.py) and
// the wired alignment /ontology diff + /alignment/diff/list payloads. Fields
// stay optional where the store column is nullable - never invent values.

export type EvolutionStepStatus = "ok" | "skipped" | "failed" | "pending";

export interface EvolutionStep {
  name: string;
  status: EvolutionStepStatus;
  detail?: string;
}

export type EvolutionRoundStatus =
  "running" | "settled" | "rolled_back" | "failed" | string;

/** Timeline row from EvolutionService.timeline (planned HTTP: /evolution/timeline). */
export interface EvolutionRoundSummary {
  round_id: string;
  at?: string | null;
  trigger_source: string;
  status: EvolutionRoundStatus;
  ops_summary?: Record<string, unknown>;
  eval_delta?: Record<string, unknown> | null;
}

/** Full report from EvolutionService.round_detail. */
export interface EvolutionRoundReport extends EvolutionRoundSummary {
  tenant_id?: string;
  steps?: EvolutionStep[];
  cost?: { tokens?: number; cny?: number; human_minutes?: number };
  edges_superseded_ids?: string[];
  rollback_of?: string | null;
  created_at?: string | null;
}

/**
 * Document-version diff row from GET /api/knowevo/alignment/diff/list
 * (wired). Serves as the timeline's real, reachable event source until the
 * evolution-round ledger HTTP route lands.
 */
export interface AlignmentDiffRow {
  diff_id: string;
  old_asset_no?: string | null;
  new_asset_no?: string | null;
  created_at?: string | null;
  change_counts?: Record<string, number>;
}

export type DiffColorKey = "added" | "deprecated" | "changed" | "other";

/** Structural shape of OntologyDiffOp without importing shared types. */
export interface OntologyDiffOpLike {
  op: string;
  target: string;
  payload?: Record<string, unknown> | null;
}

export interface DiffOpView {
  op: string;
  target: string;
  payload: Record<string, unknown>;
  colorKey: DiffColorKey;
  color: string;
  label: string;
}
