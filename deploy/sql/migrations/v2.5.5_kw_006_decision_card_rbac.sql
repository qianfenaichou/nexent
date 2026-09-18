-- KnowEvo decision-card navigation seed (T-19 wiring).
-- The decision-card route (/decisionCard) is registered in the frontend
-- ROUTE_CONFIG, but SideNavigation only renders routes present in the
-- caller's accessibleRoutes set, which is derived from the
-- VISIBILITY.LEFT_NAV_MENU permission rows (same class of "code without
-- wiring" bug as pitfall #31; seed style follows kw_004). SU and ADMIN
-- get the item; finer-grained roles stay untouched (the page itself still
-- enforces the workbench permission via _require_workbench_context, which
-- the HTTP route shares with the ontology endpoints from kw_003).
-- Ids 1518/1519: kw_004 stopped at 1511 and upstream history churns
-- 1512-1517 (v2.4.0_0721 insert / v2.4.0_0722 delete), so the kw sequence
-- continues past that band.
BEGIN;

INSERT INTO nexent.role_permission_t (
    role_permission_id, user_role, permission_category, permission_type, permission_subtype
) VALUES
    (1518, 'SU',    'VISIBILITY', 'LEFT_NAV_MENU', '/decisionCard'),
    (1519, 'ADMIN', 'VISIBILITY', 'LEFT_NAV_MENU', '/decisionCard')
ON CONFLICT (role_permission_id) DO UPDATE SET
    user_role = EXCLUDED.user_role,
    permission_category = EXCLUDED.permission_category,
    permission_type = EXCLUDED.permission_type,
    permission_subtype = EXCLUDED.permission_subtype;

COMMIT;
