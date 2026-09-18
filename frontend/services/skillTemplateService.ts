// KnowEvo skill-template API client (T-20). Thin wrapper over
// fetchWithAuth; every error surfaces as ApiError so pages can message.
//
// DATA-SOURCE STATUS (honest, T-20 integration round): the T-20 brief
// authorizes no HTTP route for skill templates - the only backend
// exposure this round is the skill_template_apply MCP tool. This client
// targets the read-only GET /api/knowevo/skill-template/list route as
// the agreed wiring point and is intentionally left UNWIRED until a
// later task owns backend/apps/knowledge_graph_app.py; until then the
// /skillTemplate page renders the pending-wiring notice instead of
// pretending to have data (never fabricated).
import { ApiError } from "./api";
import { fetchWithAuth } from "@/lib/auth";
import type { SkillTemplate } from "@/types/skillTemplate";

const fetch = fetchWithAuth;

// Not wired yet on the backend (T-20 note): knowledge_graph_app.py is
// T-19 territory and was deliberately not extended this round.
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
