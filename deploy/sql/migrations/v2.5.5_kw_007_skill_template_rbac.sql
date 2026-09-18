-- KnowEvo skill-template navigation seed (T-20 wiring).
-- The skill-template library route (/skillTemplate) is registered in the
-- frontend ROUTE_CONFIG, but SideNavigation only renders routes present in
-- the caller's accessibleRoutes set, which is derived from the
-- VISIBILITY.LEFT_NAV_MENU permission rows (seed style follows kw_006,
-- which itself follows kw_004). SU and ADMIN get the item; finer-grained
-- roles stay untouched (the template library is a read-only browse page,
-- so it carries no separate workbench permission).
-- Ids 1520/1521: kw_006 took 1518/1519 and upstream history churns
-- 1512-1517 (v2.4.0_0721 insert / v2.4.0_0722 delete), so the kw sequence
-- continues past that band. Verified unused in the live DB before insert
-- (SELECT ... WHERE role_permission_id IN (1520, 1521) -> 0 rows).
BEGIN;

INSERT INTO nexent.role_permission_t (
    role_permission_id, user_role, permission_category, permission_type, permission_subtype
) VALUES
    (1520, 'SU',    'VISIBILITY', 'LEFT_NAV_MENU', '/skillTemplate'),
    (1521, 'ADMIN', 'VISIBILITY', 'LEFT_NAV_MENU', '/skillTemplate')
ON CONFLICT (role_permission_id) DO UPDATE SET
    user_role = EXCLUDED.user_role,
    permission_category = EXCLUDED.permission_category,
    permission_type = EXCLUDED.permission_type,
    permission_subtype = EXCLUDED.permission_subtype;

COMMIT;
