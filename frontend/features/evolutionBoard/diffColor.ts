// Three-color coding frozen by the knowledgeGraph SPEC (added green /
// deprecated grey / changed gold). Shared by timeline markers and the
// node-level version diff so both surfaces paint the same op family.
import type { DiffColorKey, DiffOpView, OntologyDiffOpLike } from "./types";

export function opColorKey(op: string): DiffColorKey {
  const upper = op.toUpperCase();
  if (upper.endsWith("ADD") || upper === "ADD") return "added";
  if (upper.endsWith("DEPRECATE") || upper.endsWith("DEL") || upper === "DEL") {
    return "deprecated";
  }
  if (upper.endsWith("UPD") || upper === "UPD") return "changed";
  return "other";
}

const PAINT: Record<DiffColorKey, { color: string; label: string }> = {
  added: { color: "green", label: "added" },
  deprecated: { color: "default", label: "deprecated" },
  changed: { color: "gold", label: "changed" },
  other: { color: "default", label: "op" },
};

export function paintOp(op: string): {
  color: string;
  label: string;
  key: DiffColorKey;
} {
  const key = opColorKey(op);
  return { key, ...PAINT[key] };
}

export function toDiffOpView(op: OntologyDiffOpLike): DiffOpView {
  const paint = paintOp(op.op);
  return {
    op: op.op,
    target: op.target,
    payload: (op.payload ?? {}) as Record<string, unknown>,
    colorKey: paint.key,
    color: paint.color,
    label: paint.label,
  };
}
