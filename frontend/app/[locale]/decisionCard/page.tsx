"use client";

// Direct-URL entry for the decision-card panel (T-19). Navigation item is
// seeded for SU/ADMIN via v2.5.5_kw_006_decision_card_rbac.sql.
import DecisionCardPanel from "@/features/decisionCard/DecisionCardPanel";

export default function Page() {
  return <DecisionCardPanel />;
}
