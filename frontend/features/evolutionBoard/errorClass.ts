// Classify load/apply failures so the UI can separate "route not wired"
// from "empty list", "forbidden", "store down" and "network". Pure and
// unit-tested; never invents a success path.
export type LoadFailureKind =
  "pending_wiring" | "forbidden" | "not_found" | "server" | "network";

function statusCodeOf(err: unknown): number | null {
  if (err === null || typeof err !== "object") return null;
  const code = (err as { code?: unknown }).code;
  if (typeof code === "number" && Number.isFinite(code)) return code;
  if (typeof code === "string" && /^\d{3}$/.test(code)) return Number(code);
  return null;
}

/**
 * Map an API/network failure to a UI-safe kind.
 *
 * context="timeline" (list endpoint): a wired list never 404s, so 404/405
 * still means the HTTP surface is missing (pending_wiring).
 * context="round" (single resource): after W5 lands, 404 means the round
 * does not exist (not_found) - never "route not wired".
 *
 * - 403 = forbidden (tenant / RBAC gate, e.g. W5 cross-tenant refusal)
 * - other 4xx = not_found (bad id / bad request)
 * - 5xx = server
 * - no status = network
 */
export function classifyLoadFailure(
  err: unknown,
  context: "timeline" | "round" = "timeline"
): LoadFailureKind {
  const status = statusCodeOf(err);
  if (status === null) return "network";
  if (status === 404 || status === 405) {
    return context === "round" ? "not_found" : "pending_wiring";
  }
  if (status === 403) return "forbidden";
  if (status >= 400 && status < 500) return "not_found";
  return "server";
}

/** True when the failure means "HTTP surface not built yet". */
export function isPendingWiring(kind: LoadFailureKind): boolean {
  return kind === "pending_wiring";
}
