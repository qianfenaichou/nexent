-- Knowevo ontology workbench RBAC seed (T-08 wiring).
-- knowledge_graph_app.py requires RESOURCE.KNOWLEDGE_GRAPH.MANAGE and falls
-- back to KB.MANAGE until these rows exist (comment in that module marks
-- this as a T-08 wiring item). Seed SU + ADMIN with the explicit
-- permission so the workbench gate is semantically correct, not a fallback.
BEGIN;

INSERT INTO nexent.role_permission_t (
    role_permission_id, user_role, permission_category, permission_type, permission_subtype
) VALUES
    (1500, 'SU',    'RESOURCE', 'KNOWLEDGE_GRAPH', 'MANAGE'),
    (1501, 'ADMIN', 'RESOURCE', 'KNOWLEDGE_GRAPH', 'MANAGE')
ON CONFLICT (role_permission_id) DO UPDATE SET
    user_role = EXCLUDED.user_role,
    permission_category = EXCLUDED.permission_category,
    permission_type = EXCLUDED.permission_type,
    permission_subtype = EXCLUDED.permission_subtype;

COMMIT;
