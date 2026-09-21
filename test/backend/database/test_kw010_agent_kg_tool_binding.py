"""
Guardrail for v2.5.5_kw_010_agent_kg_tool_binding.sql (T-23 chat wiring).

Layer 1 (always runs): static contract of the migration file. The seed exists
because ``ag_tool_info_t``/``ag_tool_instance_t`` had no knowevo rows, so the
platform assistant answered every chat turn with ungrounded LLM text. These
assertions pin the properties that make the seed safe to re-run and safe to
deploy on a database that does not carry the assistant at all.

Layer 2 (RUN_POSTGRES_INTEGRATION=1): applies the migration twice against a
real PostgreSQL and asserts the runtime tool resolver
(``agents.create_agent_info._resolve_runtime_tool_records``) then returns the
five KG MCP tools for the assistant at both the draft and the published
version. Mirrors the opt-in pattern of test_knowevo_models.py.

sys.path is adjusted so ``agents.*``/``database.*`` resolve against backend/,
matching the sibling database tests.
"""
import os
import re
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../.."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../backend"))

import pytest

MIGRATION_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "deploy", "sql", "migrations",
    "v2.5.5_kw_010_agent_kg_tool_binding.sql",
)

# Exact names the platform scan produces for the knowevo surface: FastMCP
# prefixes nested mounts, and `nexent_mcp.mount(local_mcp_service, "local")`
# wraps `local_mcp_service.mount(knowevo_app, "knowevo")`.
KG_MCP_TOOL_NAMES = (
    "local_knowevo_kg_search",
    "local_knowevo_kg_stats",
    "local_knowevo_kg_multi_hop",
    "local_knowevo_decision_card_render",
    "local_knowevo_skill_template_apply",
)


def _migration_sql() -> str:
    with open(MIGRATION_PATH, "r", encoding="utf-8") as handle:
        return handle.read()


def _migration_code() -> str:
    """The SQL with ``--`` comments stripped.

    The file's header comments deliberately name the statements the structural
    assertions forbid (they explain *why* no UPDATE/DELETE is used), so those
    assertions must run against executable SQL only. The dollar-quoted JSON
    payloads never contain a ``--`` sequence.
    """
    return "\n".join(
        line.split("--", 1)[0] for line in _migration_sql().splitlines()
    )


# ---------------------------------------------------------------------------
# Layer 1: static migration contract (no database)
# ---------------------------------------------------------------------------


class TestKw010MigrationContract:
    def test_migration_file_exists_and_is_wrapped_in_one_transaction(self):
        sql = _migration_sql()
        assert sql.count("BEGIN;") == 1
        assert sql.count("COMMIT;") == 1
        assert sql.rstrip().endswith("COMMIT;")

    def test_only_additive_statements(self):
        """No UPDATE/DELETE/DROP/ALTER: the seed must never mutate existing rows."""
        sql = _migration_code()
        for forbidden in ("DROP ", "DELETE FROM", "UPDATE ", "ALTER TABLE", "TRUNCATE"):
            assert forbidden not in sql, f"kw_010 must stay additive, found {forbidden!r}"

    def test_both_inserts_are_guarded_so_a_rerun_is_a_noop(self):
        sql = _migration_code()
        # Exactly two INSERTs, each with its own NOT EXISTS guard.
        # The lookbehind keeps `CREATE SCHEMA IF NOT EXISTS` out of the count.
        assert len(re.findall(r"\bINSERT INTO\b", sql)) == 2
        assert len(re.findall(r"(?<!IF )NOT EXISTS", sql)) == 2

    def test_registers_all_five_kg_mcp_tools_under_the_scan_key(self):
        sql = _migration_sql()
        for name in KG_MCP_TOOL_NAMES:
            assert name in sql, f"{name} missing from the seed"
        # The scan keys MCP rows on (name, source, usage); matching that triple
        # is what stops a later /api/tool/scan_tool from duplicating the rows.
        assert "source = 'mcp'" in sql
        assert "'outer-apis'" in sql

    def test_tool_ids_come_from_the_sequence_not_hardcoded(self):
        """A hardcoded tool_id would desynchronise ag_tool_info_t_tool_id_seq."""
        sql = _migration_sql()
        insert_head = sql.split("INSERT INTO nexent.ag_tool_info_t", 1)[1]
        columns = insert_head.split(")", 1)[0]
        assert "tool_id" not in columns

    def test_binds_both_draft_and_published_versions(self):
        """The chat run resolves tools by the exact version_no it was launched
        with; the assistant has a draft (0) and a published snapshot (1)."""
        sql = _migration_sql()
        assert "CROSS JOIN (VALUES (0), (1)) AS v(version_no)" in sql

    def test_params_is_a_json_object_never_null(self):
        """add_tool_field calls tool_info['params'].get(...) when materialising
        an agent's tool list, so a NULL params column would break the tool."""
        sql = _migration_sql()
        assert "'{}'::json" in sql

    def test_target_is_resolved_by_agent_name_not_hardcoded(self):
        sql = _migration_code()
        assert "a.name = 'knowevo_assistant'" in sql
        # Tenant and owner are derived, so the file is portable and a no-op on
        # a database without that agent.
        assert "6756b0ab" not in sql


# ---------------------------------------------------------------------------
# Layer 2: real-Postgres integration (opt-in via RUN_POSTGRES_INTEGRATION=1)
# ---------------------------------------------------------------------------

_integration_only = pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_INTEGRATION") != "1",
    reason="set RUN_POSTGRES_INTEGRATION=1 with local PostgreSQL env",
)


def _engine_from_env():
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    url = URL.create(
        drivername="postgresql+psycopg2",
        username=os.getenv("KNOWEVO_TEST_PG_USER", "root"),
        password=os.getenv("KNOWEVO_TEST_PG_PASSWORD", os.getenv("NEXENT_POSTGRES_PASSWORD")),
        host=os.getenv("KNOWEVO_TEST_PG_HOST", os.getenv("POSTGRES_HOST", "localhost")),
        port=int(os.getenv("KNOWEVO_TEST_PG_PORT", os.getenv("POSTGRES_PORT", "5434"))),
        database=os.getenv("KNOWEVO_TEST_PG_DB", os.getenv("POSTGRES_DB", "nexent")),
    )
    return create_engine(url, future=True)


@_integration_only
def test_migration_applies_twice_and_resolver_returns_kg_tools():
    """End-to-end: seed twice (0 errors), then the resolver must offer the KG
    tools to the assistant at version_no 0 and 1."""
    from agents.create_agent_info import _resolve_runtime_tool_records
    from sqlalchemy import text

    engine = _engine_from_env()
    sql = _migration_sql()

    with engine.connect() as conn:
        conn = conn.execution_options(isolation_level="AUTOCOMMIT")
        agent = conn.execute(text(
            "SELECT agent_id, tenant_id FROM nexent.ag_tenant_agent_t "
            "WHERE name = 'knowevo_assistant' AND delete_flag <> 'Y' LIMIT 1"
        )).fetchone()
        if agent is None:
            pytest.skip("knowevo_assistant absent from this database")
        agent_id, tenant_id = int(agent[0]), str(agent[1])

        # Pass 1 and pass 2 must both succeed (idempotent seed).
        conn.execute(text(sql))
        conn.execute(text(sql))

        for version_no in (0, 1):
            names = {
                row.get("name") for row in _resolve_runtime_tool_records(
                    agent_id=agent_id, tenant_id=tenant_id, version_no=version_no,
                )
            }
            assert set(KG_MCP_TOOL_NAMES) <= names, (
                f"version_no={version_no}: resolver lost KG tools, got {sorted(names)}"
            )

        # The seed is tenant-scoped: another tenant must not see these tools.
        other_tenant = str(uuid.uuid4())
        other = _resolve_runtime_tool_records(
            agent_id=agent_id, tenant_id=other_tenant, version_no=0,
        )
        assert not any(r.get("name") in KG_MCP_TOOL_NAMES for r in other)
