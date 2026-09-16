// KnowEvo ontology workbench API client (T-05b). Thin wrapper over
// fetchWithAuth; every error surfaces as ApiError so pages can message.
import { ApiError } from "./api";
import { fetchWithAuth } from "@/lib/auth";
import type {
  OntologyDiffResult,
  OntologyMetrics,
  OntologyQueuePage,
  OntologyReviewAction,
  OntologyReviewResult,
  OntologyVersionRow,
} from "@/types/knowledgeGraph";

const fetch = fetchWithAuth;

const BASE = "/api/knowevo/ontology";

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

export const ontologyService = {
  async listProposals(params: {
    page?: number;
    pageSize?: number;
    roundId?: string;
    status?: string;
  }): Promise<OntologyQueuePage> {
    const qs = new URLSearchParams();
    if (params.page) qs.set("page", String(params.page));
    if (params.pageSize) qs.set("page_size", String(params.pageSize));
    if (params.roundId) qs.set("round_id", params.roundId);
    if (params.status) qs.set("status", params.status);
    const res = await fetch(`${BASE}/proposals?${qs.toString()}`);
    return readResponse<OntologyQueuePage>(res);
  },

  async reviewProposals(payload: {
    action: OntologyReviewAction;
    ids: string[];
    newParent?: string;
    rejectReason?: string;
  }): Promise<OntologyReviewResult> {
    const body: Record<string, unknown> = {
      action: payload.action,
      ids: payload.ids,
    };
    if (payload.newParent) body.new_parent = payload.newParent;
    if (payload.rejectReason) body.reject_reason = payload.rejectReason;
    const res = await fetch(`${BASE}/proposals/review`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return readResponse<OntologyReviewResult>(res);
  },

  async commitVersion(payload: {
    roundId?: string;
    baseVersion?: string;
    confirmedIds?: string[];
  }): Promise<OntologyVersionRow> {
    const body: Record<string, unknown> = {};
    if (payload.roundId) body.round_id = payload.roundId;
    if (payload.baseVersion) body.base_version = payload.baseVersion;
    if (payload.confirmedIds) body.confirmed_ids = payload.confirmedIds;
    const res = await fetch(`${BASE}/versions`, {
      method: "POST",
      body: JSON.stringify(body),
    });
    return readResponse<OntologyVersionRow>(res);
  },

  async versionMetrics(version: string): Promise<OntologyMetrics> {
    const res = await fetch(
      `${BASE}/versions/${encodeURIComponent(version)}/metrics`
    );
    // Endpoint wraps: {"version": ..., "metrics": {cov, red, dep, align}}.
    const data = await readResponse<{
      version: string;
      metrics: OntologyMetrics;
    }>(res);
    return data.metrics;
  },

  async activeVersion(): Promise<OntologyVersionRow | null> {
    // Read-only latest committed row; 404 before the first commit.
    const res = await fetch(`${BASE}/versions/active`);
    if (res.status === 404) {
      return null;
    }
    return readResponse<OntologyVersionRow>(res);
  },

  async diff(from: string, to: string): Promise<OntologyDiffResult> {
    const qs = new URLSearchParams({ from, to });
    const res = await fetch(`${BASE}/diff?${qs.toString()}`);
    return readResponse<OntologyDiffResult>(res);
  },
};
