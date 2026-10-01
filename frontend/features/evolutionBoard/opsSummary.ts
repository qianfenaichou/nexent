// Pure helpers over EvolutionService.ops_summary. Reserved keys are
// underscore-prefixed (_status / _steps / _edges...); every other key is
// an op-count namespace entry ({CLS_ADD: 2, REL_UPD: 5, ...}).
import type { EvolutionRoundStatus } from "./types";

export const RESERVED_KEY_PREFIX = "_";

export function isReservedOpsKey(key: string): boolean {
  return key.startsWith(RESERVED_KEY_PREFIX);
}

/** Non-reserved op counts, sorted by count desc then key for stable UI. */
export function opCounts(
  opsSummary: Record<string, unknown> | null | undefined
): Array<{ key: string; count: number }> {
  const out: Array<{ key: string; count: number }> = [];
  for (const [key, value] of Object.entries(opsSummary ?? {})) {
    if (isReservedOpsKey(key)) continue;
    // Only real numbers count. Number(null) is 0 in JS and would invent
    // a zero op-count that the ledger never wrote.
    if (typeof value !== "number" || !Number.isFinite(value)) continue;
    out.push({ key, count: value });
  }
  out.sort((a, b) => b.count - a.count || a.key.localeCompare(b.key));
  return out;
}

/** Reserved _status field, defaulting to running when absent. */
export function opsStatus(
  opsSummary: Record<string, unknown> | null | undefined
): EvolutionRoundStatus {
  const raw = (opsSummary ?? {})[`${RESERVED_KEY_PREFIX}status`];
  return typeof raw === "string" && raw ? raw : "running";
}

export function timelineStatusColor(status: string): string {
  if (status === "settled") return "green";
  if (status === "failed") return "red";
  if (status === "rolled_back") return "gray";
  return "blue";
}
