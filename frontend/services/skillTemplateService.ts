// KnowEvo skill-template API client (T-20). Thin wrapper over
// fetchWithAuth; every error surfaces as ApiError so pages can message.
//
// WIRED: GET /api/knowevo/skill-template/list is live in
// apps/knowledge_graph_app.py (read-only, workbench RBAC) - the T-20
// integration round shipped this client against the agreed contract
// while the route itself was still a pending-wiring item.
import { ApiError } from "./api";
import { fetchWithAuth } from "@/lib/auth";
import type { SkillTemplate } from "@/types/skillTemplate";

const fetch = fetchWithAuth;

const BASE = "/api/knowevo/skill-template";

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

export const skillTemplateService = {
  async listTemplates(): Promise<SkillTemplate[]> {
    const res = await fetch(`${BASE}/list`, { method: "GET" });
    const data = await readResponse<SkillTemplate[] | { templates: SkillTemplate[] }>(
      res
    );
    return Array.isArray(data) ? data : (data?.templates ?? []);
  },
};
