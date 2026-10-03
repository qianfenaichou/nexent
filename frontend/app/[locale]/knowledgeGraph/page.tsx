"use client";

// Direct-URL entry for the ontology workbench; left-nav registration lives
// in the shared navigation config - this page only mounts the feature.
import KnowledgeGraphPage from "@/features/knowledgeGraph/KnowledgeGraphPage";

export default function Page() {
  return <KnowledgeGraphPage />;
}
