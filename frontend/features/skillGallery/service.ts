// Skill-gallery API client. Listing reuses the WIRED
// GET /api/knowevo/skill-template/list via skillTemplateService.
//
// Apply (instantiate):
//   PENDING POST /api/knowevo/skill-template/apply  -> SkillTemplateService.apply_template
//   Until that route lands, apply() falls back to a
//   deterministic client render and returns via="client_preview" with an
//   explicit reason so the UI never claims reuse_count was bumped.
//   The MCP tool skill_template_apply is the real write path today.
// Honesty rules:
//   - via="server" ONLY on HTTP 200 that carries a string skill_md
//   - reuse_count is passed through only when the server sends a finite number
//   - every non-OK path returns via="client_preview" + classifyApply* reason
import { skillTemplateService } from "@/services/skillTemplateService";
import type { SkillTemplate } from "@/types/skillTemplate";
import { fetchWithAuth } from "@/lib/auth";
import {
  classifyApplyFallback,
  classifyApplyStatus,
  type ApplyFallbackReason,
} from "./errorClass";
import { renderSkillTemplate } from "./renderTemplate";

export type ApplyVia = "server" | "client_preview";
export type { ApplyFallbackReason };

export interface ApplyResult {
  name: string;
  skill_md: string;
  variables: Record<string, string>;
  /** Only present when the server actually recorded an apply. */
  reuse_count?: number;
  via: ApplyVia;
  /** Set when via=client_preview; drives the honesty Alert copy. */
  reason?: ApplyFallbackReason;
}

function clientPreview(
  name: string,
  bodyMd: string,
  variables: Record<string, string>,
  reason: ApplyFallbackReason
): ApplyResult {
  return {
    name,
    skill_md: renderSkillTemplate(bodyMd, variables, name),
    variables,
    via: "client_preview",
    reason,
  };
}

export const skillGalleryService = {
  async listTemplates(): Promise<SkillTemplate[]> {
    return skillTemplateService.listTemplates();
  },

  async applyTemplate(
    name: string,
    bodyMd: string,
    defaults: Record<string, string | undefined | null>,
    overrides: Record<string, string>
  ): Promise<ApplyResult> {
    const variables: Record<string, string> = {};
    for (const [k, v] of Object.entries(defaults)) {
      if (v !== undefined && v !== null && v !== "") variables[k] = String(v);
    }
    for (const [k, v] of Object.entries(overrides)) {
      if (v !== undefined && v !== null && v !== "") variables[k] = String(v);
    }

    let res: Response;
    try {
      res = await fetchWithAuth("/api/knowevo/skill-template/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, variables }),
      });
    } catch (err) {
      return clientPreview(name, bodyMd, variables, classifyApplyFallback(err));
    }

    if (!res.ok) {
      return clientPreview(
        name,
        bodyMd,
        variables,
        classifyApplyStatus(res.status)
      );
    }

    let data: {
      skill_md?: unknown;
      variables?: unknown;
      reuse_count?: unknown;
    } | null = null;
    try {
      data = await res.json();
    } catch {
      data = null;
    }

    // 200 without a usable skill_md is not a server apply - do not claim one.
    if (!data || typeof data.skill_md !== "string" || data.skill_md === "") {
      return clientPreview(name, bodyMd, variables, "server");
    }

    const reuseCount =
      typeof data.reuse_count === "number" && Number.isFinite(data.reuse_count)
        ? data.reuse_count
        : undefined;

    return {
      name,
      skill_md: data.skill_md,
      variables:
        data.variables && typeof data.variables === "object"
          ? (data.variables as Record<string, string>)
          : variables,
      ...(reuseCount === undefined ? {} : { reuse_count: reuseCount }),
      via: "server",
    };
  },
};
