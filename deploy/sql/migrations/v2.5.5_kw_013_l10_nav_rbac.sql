-- KnowEvo L10 navigation RBAC seed (evolutionBoard / skillGallery).
--
-- The evolution-board (/evolutionBoard) and skill-gallery (/skillGallery)
-- routes are registered in the frontend ROUTE_CONFIG, but SideNavigation
-- only renders routes present in the caller's accessibleRoutes set, which
-- is derived from the VISIBILITY.LEFT_NAV_MENU permission rows (seed style
-- follows v2.5.5_kw_007_skill_template_rbac.sql / kw_006 / kw_004). SU and
-- ADMIN get the menu items; finer-grained roles stay untouched (both pages
-- are browse surfaces; the page HTTP routes still enforce the workbench
-- permission gate via _require_workbench_context, same as /skillTemplate
-- and /decisionCard).
--
-- Ids 1522-1525: kw_007 took 1520/1521. The 1512-1517 band is churned by
-- upstream history (v2.4.0_0721 insert / v2.4.0_0722 delete) and 1600-1604
-- are taken by v2.3_merged_migrations (/space). The kw sequence continues
-- at 1522. Pre-check MUST be re-run against the live DB before insert
-- (kw_007's own header records the same discipline).
--
-- Pre-check (read-only; run before applying this migration):
--   SELECT role_permission_id, user_role, permission_subtype
--     FROM nexent.role_permission_t
--    WHERE role_permission_id IN (1522, 1523, 1524, 1525);
--   -- expect: 0 rows. If any row returns, STOP and pick a free id band.
--
--   SELECT role_permission_id, permission_subtype
--     FROM nexent.role_permission_t
--    WHERE permission_type = 'LEFT_NAV_MENU'
--      AND permission_subtype IN ('/evolutionBoard', '/skillGallery');
--   -- expect: 0 rows (idempotent double-apply is covered by ON CONFLICT
--   -- on the primary key only; a same-subtype row under a different id
--   -- would create a duplicate menu permission - hence this check).
--
-- Diff scope declaration (R7 in t08-phase2-recon-2026-09-30.md):
--   ONLY the 4 INSERT rows below. No ALTER, no DELETE, no other table.
--   Forbidden to piggyback anything else in the same migration file.

BEGIN;

INSERT INTO nexent.role_permission_t (
    role_permission_id, user_role, permission_category, permission_type, permission_subtype
) VALUES
    (1522, 'SU',    'VISIBILITY', 'LEFT_NAV_MENU', '/evolutionBoard'),
    (1523, 'ADMIN', 'VISIBILITY', 'LEFT_NAV_MENU', '/evolutionBoard'),
    (1524, 'SU',    'VISIBILITY', 'LEFT_NAV_MENU', '/skillGallery'),
    (1525, 'ADMIN', 'VISIBILITY', 'LEFT_NAV_MENU', '/skillGallery')
ON CONFLICT (role_permission_id) DO UPDATE SET
    user_role = EXCLUDED.user_role,
    permission_category = EXCLUDED.permission_category,
    permission_type = EXCLUDED.permission_type,
    permission_subtype = EXCLUDED.permission_subtype;

COMMIT;

-- Post-apply verify (read-only):
--   SELECT user_role, permission_subtype
--     FROM nexent.role_permission_t
--    WHERE permission_type = 'LEFT_NAV_MENU'
--      AND permission_subtype IN ('/evolutionBoard', '/skillGallery')
--    ORDER BY role_permission_id;
--   -- expect: 4 rows - SU/ADMIN x both subtypes, ids 1522-1525.
--
-- Rollback (only if the migration was applied and must be undone):
--   DELETE FROM nexent.role_permission_t
--    WHERE role_permission_id IN (1522, 1523, 1524, 1525);
