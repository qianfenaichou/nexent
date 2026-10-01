// KnowEvo evolution-board API client (L10). Thin wrapper over fetchWithAuth.
//
// DATA-SOURCE STATUS (do not fabricate rows):
//   WIRED   GET /api/knowevo/alignment/diff/list      - document-version events
//   WIRED   GET /api/knowevo/ontology/diff?from&to     - three-color node ops
//   WIRED   GET /api/knowevo/ontology/versions/{v}/metrics
//   PENDING GET /api/knowevo/evolution/timeline        - EvolutionService.timeline
//   PENDING GET /api/knowevo/evolution/rounds/{id}     - EvolutionService.round_detail
// The pending endpoints map 1:1 onto EvolutionService methods that already
// exist in backend/services/knowevo/evolution_service.py; only the HTTP
// route (knowledge_graph_app.py) is missing.
//
// listRounds / roundDetail return a discriminated union so the UI can
// separate "empty list" from "route not wired" from "403 tenant refusal"
// (workorder L10-R1 / W5 P0: cross-tenant reads must surface as forbidden).
import { ApiError } from "@/services/api";
import { fetchWithAuth } from "@/lib/auth";
import { classifyLoadFailure, type LoadFailureKind } from "./errorClass";
import type {
  AlignmentDiffRow,
  EvolutionRoundReport,
  EvolutionRoundSummary,
} from "./types";
import type {
  OntologyDiffResult,
  OntologyMetrics,
} from "@/types/knowledgeGraph";

const fetch = fetchWithAuth;

const ALIGN_BASE = "/api/knowevo/alignment";
const ONTOLOGY_BASE = "/api/knowevo/ontology";
const EVOLUTION_BASE = "/api/knowevo/evolution";

async function readResponse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => null);
  if (response.ok) {
    return data as T;
  }
  const detail = data?.detail;
  throw new ApiError(
    data?.code ?? response.status,
    typeof detail === "string" ? detail : response.statusText
  );
}

export type RoundsLoad =
  | { ok: true; rows: EvolutionRoundSummary[] }
  | { ok: false; kind: LoadFailureKind; detail?: string };

export type RoundDetailLoad =
  | { ok: true; report: EvolutionRoundReport }
  | { ok: false; kind: LoadFailureKind; detail?: string };

function fail(
  err: unknown,
  context: "timeline" | "round" = "timeline"
): {
  ok: false;
  kind: LoadFailureKind;
  detail?: string;
} {
  const kind = classifyLoadFailure(err, context);
  const detail = err instanceof Error ? err.message : undefined;
  return { ok: false, kind, detail };
}

export const evolutionBoardService = {
  async listAlignmentDiffs(limit = 50): Promise<AlignmentDiffRow[]> {
    const res = await fetch(`${ALIGN_BASE}/diff/list?limit=${limit}`, {
      method: "GET",
    });
    const data = await readResponse<
      AlignmentDiffRow[] | { diffs: AlignmentDiffRow[] }
    >(res);
    return Array.isArray(data) ? data : (data?.diffs ?? []);
  },

  async ontologyDiff(from: string, to: string): Promise<OntologyDiffResult> {
    const qs = new URLSearchParams({ from, to });
    const res = await fetch(`${ONTOLOGY_BASE}/diff?${qs.toString()}`);
    return readResponse<OntologyDiffResult>(res);
  },

  async versionMetrics(version: string): Promise<OntologyMetrics> {
    const res = await fetch(
      `${ONTOLOGY_BASE}/versions/${encodeURIComponent(version)}/metrics`
    );
    return readResponse<OntologyMetrics>(res);
  },

  /** PENDING wiring: EvolutionService.timeline(tenant_id, since). */
  async listRounds(since?: string): Promise<RoundsLoad> {
    try {
      const qs = new URLSearchParams();
      if (since) qs.set("since", since);
      const suffix = qs.toString() ? `?${qs.toString()}` : "";
      const res = await fetch(`${EVOLUTION_BASE}/timeline${suffix}`);
      const data = await readResponse<
        EvolutionRoundSummary[] | { rounds: EvolutionRoundSummary[] }
      >(res);
      const rows = Array.isArray(data) ? data : (data?.rounds ?? []);
      return { ok: true, rows };
    } catch (err) {
      return fail(err, "timeline");
    }
  },

  /**
   * EvolutionService.round_detail(round_id). Wired (L10-W5/α) with a
   * tenant gate: 403 = other tenant, 404 = unknown round (not "pending").
   */
  async roundDetail(roundId: string): Promise<RoundDetailLoad> {
    try {
      const res = await fetch(
        `${EVOLUTION_BASE}/rounds/${encodeURIComponent(roundId)}`
      );
      const report = await readResponse<EvolutionRoundReport>(res);
      return { ok: true, report };
    } catch (err) {
      return fail(err, "round");
    }
  },
};
