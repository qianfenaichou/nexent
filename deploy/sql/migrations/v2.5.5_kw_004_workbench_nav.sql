-- Knowevo ontology workbench navigation seed (T-18a wiring).
-- The workbench route (/knowledgeGraph) is registered in the frontend
-- ROUTE_CONFIG, but SideNavigation only renders routes present in the
-- caller's accessibleRoutes set, which is derived from the
-- VISIBILITY.LEFT_NAV_MENU permission rows. Without this seed the menu
-- item exists in code but never shows for any role - the same class of
-- "code without wiring" bug as pitfall #31. SU and ADMIN get the item;
-- finer-grained roles stay untouched (the page itself still enforces
-- RESOURCE.KNOWLEDGE_GRAPH.MANAGE from kw_003).
BEGIN;

INSERT INTO nexent.role_permission_t (
    role_permission_id, user_role, permission_category, permission_type, permission_subtype
) VALUES
    (1510, 'SU',    'VISIBILITY', 'LEFT_NAV_MENU', '/knowledgeGraph'),
    (1511, 'ADMIN', 'VISIBILITY', 'LEFT_NAV_MENU', '/knowledgeGraph')
ON CONFLICT (role_permission_id) DO UPDATE SET
    user_role = EXCLUDED.user_role,
    permission_category = EXCLUDED.permission_category,
    permission_type = EXCLUDED.permission_type,
    permission_subtype = EXCLUDED.permission_subtype;

COMMIT;
