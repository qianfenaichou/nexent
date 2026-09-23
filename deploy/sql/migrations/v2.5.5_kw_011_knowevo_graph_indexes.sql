-- KnowEvo graph write-path support indexes (kw_011).
--
-- WHY these indexes exist (and why they are shaped this way):
--
-- The graph write path (PgJsonbGraphStore.upsert_entities /
-- upsert_relations) looks up the *current view* of a row before deciding
-- whether to insert, update, or no-op. "Current view" is the bi-temporal
-- predicate valid_now(model):  valid_at <= now()  AND
-- (invalid_at IS NULL OR invalid_at > now()).  That predicate is applied to
-- the EXISTING-row lookup for relations, and an equivalent (tenant_id,
-- stable_id) existence check is applied for entities.
--
-- The pre-existing index `ix_kr_valid(valid_at, invalid_at)` is a plain
-- b-tree. A b-tree can only use the `valid_at <= t_v` prefix of the
-- predicate; the second conjunct `invalid_at IS NULL OR invalid_at > t_v`
-- is NOT usable as a b-tree range/equality condition (NULL participates in
-- neither a range scan nor an equality). So the planner falls back to a seq
-- scan / full index scan for the invalid_at half, which is exactly the
-- bottleneck the batched upsert exposes on large graphs (tens of thousands
-- of rows, one lookup per batch key).
--
-- The logically identical test is the half-open tstzrange containment
--   [valid_at, COALESCE(invalid_at, 'infinity')) @> t_v
-- which is a GiST operator on a range type. A GiST index on that range
-- expression answers the full current-view predicate in one index hit, so
-- the batched lookups stay O(log n) instead of O(n). The entity table gets
-- the same treatment so entity upserts benefit identically.
--
-- `ix_kr_current` is a partial b-tree (WHERE invalid_at IS NULL) covering
-- the very common "is this (tenant, src, rel_type) already live?" probe with
-- a tiny, write-cheap index. `ix_kr_hop_rev_cover` adds an INCLUDE (src) so
-- the reverse-hop lookup (by dst) can return src straight from the index
-- without a heap fetch.
--
-- HONEST TRADE-OFFS (do not hide these):
--   * These indexes make WRITES slower: every INSERT/UPDATE must also update
--     the GiST range index and the partial b-tree. That is the deliberate
--     cost of speeding up the current-view lookups the write path depends
--     on. Measured speedup of the lookup must be weighed against this.
--   * If the migration runner wraps DDL in a transaction (BEGIN/COMMIT), the
--     GiST indexes below CANNOT be created CONCURRENTLY - a concurrent build
--     requires its own transaction and would error inside an outer BEGIN.
--     They are therefore built normally (brief write-lock on the table).
--   * All four are CREATE INDEX IF NOT EXISTS, so re-running this file is a
--     no-op and it is safe to ship alongside the core migration.
BEGIN;

CREATE SCHEMA IF NOT EXISTS nexent;

SET LOCAL search_path TO nexent, public;

-- 1. GiST on the bi-temporal range for kg_relation_t current-view lookups.
CREATE INDEX IF NOT EXISTS ix_kr_valid_range
    ON nexent.kg_relation_t
    USING GIST (tstzrange(valid_at, COALESCE(invalid_at, 'infinity'::timestamptz), '[)'));

-- 2. Same GiST range index for kg_entity_t (entity upsert existence probe).
CREATE INDEX IF NOT EXISTS ix_ke_valid_range
    ON nexent.kg_entity_t
    USING GIST (tstzrange(valid_at, COALESCE(invalid_at, 'infinity'::timestamptz), '[)'));

-- 3. Partial b-tree: only live rows, for the (tenant, src, rel_type) probe.
CREATE INDEX IF NOT EXISTS ix_kr_current
    ON nexent.kg_relation_t (tenant_id, src, rel_type)
    WHERE invalid_at IS NULL;

-- 4. Covering reverse-hop index: answer dst-rooted walks from the index.
CREATE INDEX IF NOT EXISTS ix_kr_hop_rev_cover
    ON nexent.kg_relation_t (tenant_id, dst, rel_type) INCLUDE (src);

-- 5. Data invariant required by the range-containment predicate
--    (knowevo_db.valid_range_contains). PREFLIGHT - run this FIRST and expect 0:
--
--      SELECT 'kg_relation_t' AS tbl, count(*) FROM nexent.kg_relation_t
--        WHERE invalid_at IS NOT NULL AND invalid_at < valid_at
--      UNION ALL
--      SELECT 'kg_entity_t', count(*) FROM nexent.kg_entity_t
--        WHERE invalid_at IS NOT NULL AND invalid_at < valid_at;
--
--    Why it matters: the existing boolean predicate
--    valid_at <= t_v AND (invalid_at IS NULL OR invalid_at > t_v) returns
--    FALSE for such a row, but *constructing* the equivalent
--    tstzrange(valid_at, invalid_at) RAISES
--    "range lower bound must be less than or equal to range upper bound".
--    So a degenerate row is a silent non-match today and a hard error under
--    the range form. NOT VALID keeps the migration from failing on legacy
--    rows while still enforcing the invariant on every new row; validate with
--    ALTER TABLE ... VALIDATE CONSTRAINT once the preflight returns 0.
ALTER TABLE nexent.kg_relation_t
    ADD CONSTRAINT ck_kr_valid_order
    CHECK (invalid_at IS NULL OR invalid_at >= valid_at) NOT VALID;

ALTER TABLE nexent.kg_entity_t
    ADD CONSTRAINT ck_ke_valid_order
    CHECK (invalid_at IS NULL OR invalid_at >= valid_at) NOT VALID;

COMMIT;
