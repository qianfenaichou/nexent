-- Knowevo T-21 alignment impact-surface indexes (kw_008).
-- 02-tech-plan 4.2 DECISION: the impact surface is answered by TWO index
-- lookups, never an online graph traversal. This file adds the two indexes
-- that serve those lookups. The 12 domain tables are schema-frozen (03-plan
-- 3.3; the brief for T-21 also fixes this as zero-ALTER), so it contains
-- CREATE INDEX only -- no ALTER, no new table -- and is idempotent through
-- IF NOT EXISTS, safe to re-run after a checksum change.
--
-- (a) kg_evidence_t - "evidence span falls in the changed paragraph set dS"
--     (02-tech-plan 4.2 step 1). The span location is NOT a dedicated
--     column: kg_evidence_t carries it as span_loc JSONB
--     ({chunk_idx, page, bbox?}) alongside span_text TEXT (knowevo_db.py
--     KgEvidence, DDL from kw_001). dS is a set of paragraph/chunk indexes
--     belonging to one document, so the anchor predicate is the doc id plus
--     an IN-list of chunk indexes:
--       WHERE doc_id = :new_doc
--         AND (span_loc ->> 'chunk_idx') = ANY(:ds_chunk_idx::text[])
--     A composite btree on the extracted scalar answers that in one index
--     scan. A GIN containment index over span_loc was considered and
--     rejected: span_loc holds no JSONB array (the JSONB-array-GIN case does
--     not apply), and containment cannot combine with the doc_id equality in
--     a single index, so set membership would degrade to OR-expansion plus a
--     bitmap AND against ix_kev_doc (kw_001), which already covers the doc
--     filter on its own.
--
-- (b) decision_card_t - "which cards cite an entity through kg_path"
--     (02-tech-plan 4.2 step 2, the entity -> decision_card_id inverted
--     lookup). payload JSONB holds the card; the citation lives at
--     payload.candidates[*].evidence_chain[*].provenance.kg_path[*]
--     (schemas.py Candidate / EvidenceItem / Provenance; kg_path is a list
--     of readable entity/claim strings). ix_dc_stamp (kw_001) already
--     indexes the whole payload with jsonb_path_ops for knowledge-stamp
--     containment. This file adds a tighter jsonb_path_ops GIN scoped to the
--     candidates subtree -- where every kg_path lives -- as the purpose-built
--     inverted index for the reverse lookup:
--       WHERE (payload -> 'candidates')
--             @> '[{"evidence_chain":[{"provenance":{"kg_path":["<entity>"]}}]}]'
--     It is deliberately not a duplicate of ix_dc_stamp: same operator class,
--     but a narrower subtree and therefore a smaller index.
BEGIN;

-- Self-guard: init.sql creates the schema, but this file must also run
-- standalone (verified against a clean database) and stay idempotent
-- (same guard as kw_001 / kw_002).
CREATE SCHEMA IF NOT EXISTS nexent;

-- (a) evidence span -> changed paragraph set dS (doc-anchored chunk lookup)
CREATE INDEX IF NOT EXISTS ix_kev_span_chunk
    ON nexent.kg_evidence_t (doc_id, (span_loc ->> 'chunk_idx'));

-- (b) entity -> citing decision card, via the evidence_chain kg_path walk
CREATE INDEX IF NOT EXISTS ix_dc_kg_path
    ON nexent.decision_card_t USING gin ((payload -> 'candidates') jsonb_path_ops);

COMMIT;
