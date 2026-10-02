// Classify load/apply failures so the UI can separate "route not wired"
// from "empty list", "login expired", "not permitted", "store down" and
// "network". Pure and unit-tested; never invents a success path.
export type LoadFailureKind =
  | "pending_wiring"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "server"
  | "network";

/**
 * Codes that mean "the credentials behind this call are gone": HTTP 401
 * plus the auth business codes the request layer raises for missing,
 * expired or invalid sessions (the client-side pre-check throws one of
 * those before any HTTP call is made).
 */
const UNAUTHORIZED_CODES = new Set([
  "401",
  "1002",
  "1003",
  "1004",
  "000201",
  "000203",
  "000204",
]);

/** Codes that mean "logged in, but this tenant or role may not read it". */
const FORBIDDEN_CODES = new Set(["403", "000202"]);

function codeOf(err: unknown): string | null {
  if (err === null || typeof err !== "object") return null;
  const code = (err as { code?: unknown }).code;
  if (typeof code === "number" && Number.isFinite(code)) return String(code);
  if (typeof code === "string" && /^\d+$/.test(code.trim())) return code.trim();
  return null;
}

/** HTTP status only: a 3-digit code. Business codes are 4-6 digits. */
function statusCodeOf(code: string): number | null {
  return /^\d{3}$/.test(code) ? Number(code) : null;
}

/**
 * Map an API/network failure to a UI-safe kind.
 *
 * context="timeline" (list endpoint): a wired list never 404s, so 404/405
 * still means the HTTP surface is missing (pending_wiring).
 * context="round" (single resource): 404 means the round does not exist
 * (not_found) - never "route not wired".
 *
 * - 401 / expired or invalid session = unauthorized (re-login, not "missing")
 * - 403 = forbidden (tenant / RBAC gate: the caller is authenticated but
 *   may not read this tenant's rounds)
 * - other 3-digit 4xx = not_found (bad id / bad request)
 * - 5xx, and any business code without HTTP meaning = server
 * - no code at all = network
 */
export function classifyLoadFailure(
  err: unknown,
  context: "timeline" | "round" = "timeline"
): LoadFailureKind {
  const code = codeOf(err);
  if (code === null) return "network";
  if (UNAUTHORIZED_CODES.has(code)) return "unauthorized";
  if (FORBIDDEN_CODES.has(code)) return "forbidden";
  const status = statusCodeOf(code);
  if (status === null) return "server";
  if (status === 404 || status === 405) {
    return context === "round" ? "not_found" : "pending_wiring";
  }
  if (status >= 400 && status < 500) return "not_found";
  return "server";
}

/** True when the failure means "HTTP surface not built yet". */
export function isPendingWiring(kind: LoadFailureKind): boolean {
  return kind === "pending_wiring";
}
