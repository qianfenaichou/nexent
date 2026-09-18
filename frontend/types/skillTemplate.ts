// KnowEvo skill-template types (T-20). Mirrors the skill_template_t row
// (backend/database/knowevo_db.py SkillTemplate) as returned by
// SkillTemplateService.list_templates / get_template - the same shape the
// skill_template_apply MCP tool instantiates. No re-modeling of the
// service payload: fields are optional where the column is nullable.

export interface SkillTemplateVariables {
  domain?: string;
  task_type?: string;
  relation_template?: string;
  domain_rules?: string;
  // The service merges arbitrary stored defaults; unknown keys survive.
  [key: string]: string | undefined;
}

export interface SkillTemplateSource {
  pattern?: string;
  mined_from?: string[];
  induced_at?: string;
  induced_by?: string; // llm | deterministic
  support?: number;
  [key: string]: unknown;
}

export interface SkillTemplate {
  id?: string;
  name: string;
  task_type: string;
  domain: string | null;
  version: string;
  // Pre-render template markdown (frontmatter + body): what the page
  // previews and copies - NOT a rendered instance (apply lives behind the
  // skill_template_apply MCP tool).
  body_md: string;
  variables: SkillTemplateVariables | null;
  source: SkillTemplateSource | null;
  reuse_count: number | null;
  // Only written by record_reuse_outcome after a real run; null means
  // "never recorded", displayed as such (never as 0%).
  reuse_success: number | null;
  avg_edit_distance: number | null;
  created_at?: string;
}
