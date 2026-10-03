// Classify skill-gallery apply fallbacks. The honesty contract is: a failed
// server apply must fall back to local render WITHOUT claiming reuse_count
// was bumped. The reason string is what the UI shows - never a silent success.
export type ApplyFallbackReason =
  "route_pending" | "template_missing" | "forbidden" | "server" | "network";

function statusCodeOf(err: unknown): number | null {
  if (err === null || typeof err !== "object") return null;
  const code = (err as { code?: unknown }).code;
  if (typeof code === "number" && Number.isFinite(code)) return code;
  if (typeof code === "string" && /^\d{3}$/.test(code)) return Number(code);
  return null;
}

/**
 * Map a failed POST /skill-template/apply to a fallback reason.
 *
 * After W10 lands, HTTP 404 is "template not found" (the route's KeyError
 * mapping), NOT "route not wired" - copy must not claim pending wiring.
 * 405 is still the unwired/disabled-method signal.
 *
 * - 404: template_missing
 * - 405: route_pending
 * - 403: RBAC / tenant refusal
 * - other 4xx: template_missing (bad body)
 * - 5xx: store down
 * - no status: network
 */
export function classifyApplyFallback(err: unknown): ApplyFallbackReason {
  const status = statusCodeOf(err);
  if (status === null) return "network";
  return classifyApplyStatus(status);
}

/** Map a non-OK Response status (when fetch did not throw). */
export function classifyApplyStatus(status: number): ApplyFallbackReason {
  if (status === 405) return "route_pending";
  if (status === 404) return "template_missing";
  if (status === 403) return "forbidden";
  if (status >= 400 && status < 500) return "template_missing";
  return "server";
}
