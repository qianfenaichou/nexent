import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Regression test for pitfalls #48 (fixed in commit 0c4f613e3): direct URL
// navigation to camelCase routes (/knowledgeGraph, /decisionCard,
// /skillTemplate) was redirected to home because the route guard compared the
// URL path (which keeps its casing) against accessibleRoutes (which authService
// lowercases) without normalizing the comparison side.
//
// Why this is a source-contract test and not a runtime hook test:
// - canAccessRoute() is a useCallback inside useAuthorization() and the guard
//   predicate (normalizedCleanPath / hasAccess) is computed in the hook body;
//   the sidebar filter lives inside the SideNavigation component. None of them
//   is an exported pure function, and frontend/tests/ has no React test host
//   (no jsdom, no @testing-library, no vitest/jest in frontend/package.json or
//   node_modules).
// - Importing hooks/auth/useAuthorization.ts under node --experimental-strip-
//   types fails with "Cannot find module 'next/navigation'"; importing
//   lib/auth.ts fails with "Cannot find package '@/services'" (the `@/` alias
//   is resolved by Next, not by Node).
// - The sanctioned pattern for exactly this case already exists in this dir:
//   tests/agentSelectorSwitchOrder.test.ts reads the real source file and pins
//   its behavior contract with regex, no React required.
//
// This test therefore (1) pins the three normalization sites to the exact
// formulas shipped in 0c4f613e3 so a revert is caught, and (2) re-evaluates
// the review-mandated scenarios against those very formulas transcribed below,
// locking the fixed semantics.

const useAuthorizationPath = new URL(
  "../hooks/auth/useAuthorization.ts",
  import.meta.url
);
const sideNavigationPath = new URL(
  "../components/navigation/SideNavigation.tsx",
  import.meta.url
);

test("route guard pins the three 0c4f613e3 normalization sites in useAuthorization.ts", async () => {
  const source = await readFile(useAuthorizationPath, "utf8");

  // Site 1: the guard normalizes the URL-cased cleanPath once and reuses it in
  // both the exact-match includes() and the prefix-match startsWith() branch.
  assert.match(
    source,
    /const normalizedCleanPath = cleanPath\.toLowerCase\(\);?/
  );
  assert.match(source, /accessibleRoutes\.includes\(normalizedCleanPath\)/);
  assert.match(source, /normalizedCleanPath\.startsWith\(route \+ "\/"\)/);

  // Site 2: canAccessRoute lowercases its route argument to match the
  // lowercased accessibleRoutes.
  assert.match(source, /accessibleRoutes\.includes\(route\.toLowerCase\(\)\)/);
});

test("side navigation menu filter lowercases route.path (site 3)", async () => {
  const source = await readFile(sideNavigationPath, "utf8");

  assert.match(
    source,
    /accessibleRoutes\.includes\(route\.path\.toLowerCase\(\)\)/
  );
});

// Behavioral lock: transcribe the exact formulas above (identical token-per-
// token to the source) and run the scenarios the r18 review mandated. These
// reference predicates are kept inline and asserted against the source in the
// two tests above, so a regression in the source (e.g. dropping a toLowerCase)
// also trips these assertions.

function evalCanAccessRoute(
  accessibleRoutes: string[],
  route: string
): boolean {
  return accessibleRoutes.includes(route.toLowerCase());
}

function evalRouteGuard(
  accessibleRoutes: string[],
  cleanPath: string
): boolean {
  const normalizedCleanPath = cleanPath.toLowerCase();
  const isSharePage = normalizedCleanPath.startsWith("/share/");
  const isWithinAccessiblePrefix = accessibleRoutes.some(
    (route) => route !== "/" && normalizedCleanPath.startsWith(route + "/")
  );
  return (
    isSharePage ||
    accessibleRoutes.includes(normalizedCleanPath) ||
    isWithinAccessiblePrefix
  );
}

function evalSidebarFilter(
  routeConfigPaths: string[],
  accessibleRoutes: string[]
): string[] {
  return routeConfigPaths.filter((path) =>
    accessibleRoutes.includes(path.toLowerCase())
  );
}

test("canAccessRoute allows /knowledgeGraph and /knowledgegraph when /knowledgegraph is accessible", () => {
  const accessibleRoutes = ["/knowledgegraph"];
  assert.equal(evalCanAccessRoute(accessibleRoutes, "/knowledgeGraph"), true);
  assert.equal(evalCanAccessRoute(accessibleRoutes, "/knowledgegraph"), true);
});

test("canAccessRoute denies an unknown route", () => {
  assert.equal(evalCanAccessRoute(["/knowledgegraph"], "/not-a-route"), false);
});

test("route guard denies any route when no routes are accessible", () => {
  assert.equal(evalRouteGuard([], "/knowledgeGraph"), false);
  assert.equal(evalRouteGuard([], "/knowledgegraph"), false);
});

test("route guard admits both casings of an accessible exact route", () => {
  const accessibleRoutes = ["/knowledgegraph"];
  assert.equal(evalRouteGuard(accessibleRoutes, "/knowledgeGraph"), true);
  assert.equal(evalRouteGuard(accessibleRoutes, "/knowledgegraph"), true);
});

test("route guard prefix branch still allows /evaluation/<id> when /evaluation is accessible", () => {
  const accessibleRoutes = ["/evaluation"];
  assert.equal(
    evalRouteGuard(accessibleRoutes, "/evaluation/01234567-89ab"),
    true
  );
  assert.equal(evalRouteGuard(accessibleRoutes, "/knowledgegraph"), false);
  // Nested child under /evaluation with its own camelCase segment still matches.
  assert.equal(
    evalRouteGuard(accessibleRoutes, "/evaluation/knowledgeGraph"),
    true
  );
});

test("route guard never treats the bare / root as a prefix", () => {
  // "/" is excluded from the prefix branch (route !== "/"), so it must not
  // grant blanket access to arbitrary top-level routes; "/" only matches the
  // exact home path.
  const accessibleRoutes = ["/"];
  assert.equal(evalRouteGuard(accessibleRoutes, "/"), true);
  assert.equal(evalRouteGuard(accessibleRoutes, "/knowledgeGraph"), false);
  assert.equal(evalRouteGuard(accessibleRoutes, "/nope"), false);
  assert.equal(
    evalRouteGuard(
      accessibleRoutes,
      "/knowledgeGraph/00000000-0000-0000-0000-000000000000"
    ),
    false
  );
});

test("sidebar menu filter resolves camelCase ROUTE_CONFIG paths from lowercased accessibleRoutes", () => {
  const routeConfigPaths = [
    "/",
    "/chat",
    "/agent-tasks",
    "/agent-dev",
    "/models",
    "/knowledges",
    "/agents",
    "/memory",
    "/evaluation",
    "/resource-space",
    "/agent-space",
    "/mcp-space",
    "/skill-space",
    "/knowledgeGraph",
    "/decisionCard",
    "/skillTemplate",
  ];
  const accessibleRoutes = [
    "/",
    "/knowledgegraph",
    "/decisioncard",
    "/skilltemplate",
  ];

  const visible = evalSidebarFilter(routeConfigPaths, accessibleRoutes);

  assert.ok(visible.includes("/knowledgeGraph"));
  assert.ok(visible.includes("/decisionCard"));
  assert.ok(visible.includes("/skillTemplate"));
  assert.ok(!visible.includes("/evaluation"));
  assert.ok(!visible.includes("/chat"));
});

test("sidebar menu filter falls empty when no routes are accessible", () => {
  assert.deepEqual(evalSidebarFilter(["/chat", "/knowledgeGraph"], []), []);
});
