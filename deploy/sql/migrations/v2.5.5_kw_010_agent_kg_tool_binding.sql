-- KnowEvo T-23 chat QA wiring: register + bind the self-built KG MCP tools to
-- the platform assistant (kw_010).
--
-- Why this seed exists (observed DB state, 2026-09-22, tenant 6756b0ab):
--   * nexent.ag_tool_info_t held 34 rows with ZERO knowevo tools - the five
--     tools mounted by backend/tool_collection/mcp/local_mcp_service.py
--     (kg_search / kg_stats / kg_multi_hop / decision_card_render /
--     skill_template_apply) were never offered to any agent, so the chat path
--     could only fall back to an ungrounded LLM answer.
--   * nexent.ag_tool_instance_t held 0 rows, so `knowevo_assistant` (agent_id=1)
--     had no tool at all. `_resolve_runtime_tool_records`
--     (backend/agents/create_agent_info.py) reads exactly this table
--     (enabled rows for agent_id + tenant_id + version_no), which is why
--     every chat turn produced plain text with no evidence chain and no
--     knowledge-version stamp.
--
-- Naming: the platform scans the built-in "outer-apis" MCP server
-- (backend/services/tool_configuration_service.py, urljoin(NEXENT_MCP_SERVER,
-- "sse")) whose surface is `mcp_service.nexent_mcp`. FastMCP prefixes nested
-- mounts, and `nexent_mcp.mount(local_mcp_service, "local")` wraps
-- `local_mcp_service.mount(knowevo_app, "knowevo")`, so the scanned names are
-- `local_knowevo_<tool>` (verified in-process via `nexent_mcp.get_tools()`).
-- The pre-existing row `local_test_tool_name` (tool_id=34, usage=outer-apis)
-- proves the same surface and prefixing rule.
--
-- Rows are keyed on the same (name, source, usage) triple the platform scan
-- uses (backend/database/tool_db.py), so a later `GET /api/tool/scan_tool`
-- UPDATES these rows in place instead of creating duplicates.
--
-- Contract / honesty notes:
--   * Additive + idempotent only: no UPDATE of pre-existing rows, no DELETE,
--     no DDL; guarded by NOT EXISTS, so re-running is a no-op.
--   * The agent/tenant/owner are resolved from `ag_tenant_agent_t` by name
--     instead of being hard-coded; on a database without that agent the whole
--     file is a silent no-op.
--   * `params` must be a JSON object, never NULL: `add_tool_field`
--     (backend/database/tool_db.py) calls `tool_info["params"].get(...)` when
--     materialising an agent's tool list.
--   * PRECONDITION (operational, not enforced here): the MCP server that
--     serves these rows must be reachable from the runtime - start it with
--     `python -m mcp_service` (FastMCP SSE on 5011) and point the backend env
--     `NEXENT_MCP_SERVER` at a host-resolvable URL. Binding an unreachable MCP
--     tool would turn a plain-text answer into a hard agent-run failure.
--
-- `inputs` below is the tool's exposed property schema as valid JSON (the
-- platform scan stores a Python-dict `str`, which `json.loads` rejects; valid
-- JSON is accepted by both the local and the MCP tool-construction paths).
BEGIN;

CREATE SCHEMA IF NOT EXISTS nexent;

SET LOCAL search_path TO nexent, public;

-- 1. Register the five knowevo MCP tools (no explicit tool_id: the table's
--    sequence stays the single id source, matching the platform scan).
WITH tools(name, description, inputs) AS (
    VALUES
        (
            'local_knowevo_kg_search',
            'Search the knowledge graph: lexical entity lookup + neighborhood '
            '(current view).',
            $json${"inputs": {"type": "object", "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 200}, "hop": {"type": "integer", "minimum": 1, "maximum": 2, "default": 1}, "top_k": {"type": "integer", "minimum": 1, "maximum": 20, "default": 5}, "ontology_version": {"type": "string", "default": null}}, "required": ["query"]}}$json$
        ),
        (
            'local_knowevo_kg_stats',
            'Knowledge graph scale numbers.',
            $json${"inputs": {"type": "object", "properties": {"scope": {"type": "string", "pattern": "^(graph|full)$", "default": "graph"}}}}$json$
        ),
        (
            'local_knowevo_kg_multi_hop',
            'Version-pinned multi-hop walk: collect evidence paths constrained '
            'to a knowledge version.',
            $json${"inputs": {"type": "object", "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 500}, "seeds": {"type": "array", "items": {"type": "string"}, "maxItems": 10}, "depth": {"type": "integer", "minimum": 1, "maximum": 3, "default": 3}, "beam": {"type": "integer", "minimum": 1, "maximum": 3, "default": 3}, "ontology_version": {"type": "string", "default": null}, "top_k": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}, "required": ["question"]}}$json$
        ),
        (
            'local_knowevo_decision_card_render',
            'Render a decision card for one question: candidates with evidence '
            'chains, confidence, risks, counterfactual, knowledge-version stamp '
            'and conflict adjudications. Refuses with INSUFFICIENT_EVIDENCE when '
            'no evidence supports the question.',
            $json${"inputs": {"type": "object", "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 500}, "ontology_version": {"type": "string", "default": null}, "mode": {"type": "string", "pattern": "^(full|lite)$", "default": "full"}}, "required": ["question"]}}$json$
        ),
        (
            'local_knowevo_skill_template_apply',
            'Instantiate a mined SKILL.md template: render its parameterized '
            'body with the given variables and bump the reuse counter. The '
            'success rate is not written here - apply cannot know the outcome.',
            $json${"inputs": {"type": "object", "properties": {"template_name": {"type": "string", "minLength": 1, "maxLength": 64}, "variables": {"type": "object", "additionalProperties": {"type": "string"}, "maxProperties": 16}}, "required": ["template_name"]}}$json$
        )
)
INSERT INTO nexent.ag_tool_info_t (
    name, class_name, description, source, author, usage, params, inputs,
    output_type, is_available, origin_name, is_user_selectable, delete_flag
)
SELECT
    t.name::varchar,
    t.name::varchar,
    t.description::varchar,
    'mcp',
    a.tenant_id,
    'outer-apis',
    '[]'::json,
    t.inputs::varchar,
    'object',
    true,
    t.name::varchar,
    true,
    'N'
FROM tools AS t
CROSS JOIN (
    SELECT DISTINCT a.tenant_id
    FROM nexent.ag_tenant_agent_t AS a
    WHERE a.name = 'knowevo_assistant' AND a.delete_flag <> 'Y'
) AS a
WHERE NOT EXISTS (
    SELECT 1
    FROM nexent.ag_tool_info_t AS x
    WHERE x.name = t.name
      AND x.source = 'mcp'
      AND x.usage = 'outer-apis'
      AND x.delete_flag <> 'Y'
);

-- 2. Bind those tools to the knowevo assistant for BOTH the draft (0) and the
--    published snapshot (1): the chat run resolves tools by the exact
--    version_no it was launched with, and the agent is published at version 1
--    (ag_tenant_agent_t.current_version_no) while its draft is version 0.
--    `params` starts empty; per-instance overrides are written later by
--    `POST /api/tool/update`.
INSERT INTO nexent.ag_tool_instance_t (
    tool_id, agent_id, params, user_id, tenant_id, enabled, version_no,
    created_by, updated_by, delete_flag
)
SELECT
    ti.tool_id,
    ag.agent_id,
    '{}'::json,
    ag.owner_id,
    ag.tenant_id,
    true,
    v.version_no,
    ag.owner_id,
    ag.owner_id,
    'N'
FROM nexent.ag_tool_info_t AS ti
CROSS JOIN (
    SELECT DISTINCT
        a.agent_id,
        a.tenant_id,
        COALESCE(
            a.created_by,
            (
                SELECT u.user_id
                FROM nexent.user_tenant_t AS u
                WHERE u.tenant_id = a.tenant_id AND u.user_role = 'ADMIN'
                LIMIT 1
            )
        ) AS owner_id
    FROM nexent.ag_tenant_agent_t AS a
    WHERE a.name = 'knowevo_assistant' AND a.delete_flag <> 'Y'
) AS ag
CROSS JOIN (VALUES (0), (1)) AS v(version_no)
WHERE ti.source = 'mcp'
  AND ti.usage = 'outer-apis'
  AND ti.delete_flag <> 'Y'
  AND ti.name IN (
      'local_knowevo_kg_search',
      'local_knowevo_kg_stats',
      'local_knowevo_kg_multi_hop',
      'local_knowevo_decision_card_render',
      'local_knowevo_skill_template_apply'
  )
  AND NOT EXISTS (
      SELECT 1
      FROM nexent.ag_tool_instance_t AS x
      WHERE x.agent_id = ag.agent_id
        AND x.tool_id = ti.tool_id
        AND x.tenant_id = ag.tenant_id
        AND x.version_no = v.version_no
        AND x.delete_flag <> 'Y'
  );

COMMIT;
