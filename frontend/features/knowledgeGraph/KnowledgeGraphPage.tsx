"use client";

// Ontology workbench shell (T-05b): tabbed container over the proposal
// queue, the G6 tree, and the diff+quality panel. Route: /knowledgeGraph
// (direct-URL acceptance; official nav registration belongs to T-12).
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Tabs } from "antd";
import { ProposalQueue } from "./components/ProposalQueue";
import { OntologyTreePanel } from "./components/OntologyTreePanel";
import { DiffAndQualityPanel } from "./components/DiffAndQualityPanel";
import type { OntologyVersionRow } from "@/types/knowledgeGraph";

export default function KnowledgeGraphPage() {
  const { t } = useTranslation();
  const [active, setActive] = useState("queue");
  const [committedRow, setCommittedRow] = useState<OntologyVersionRow | null>(
    null
  );

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6">
      <h1 className="mb-1 text-xl font-semibold">
        {t("knowledgeGraph.title", { defaultValue: "本体工作台" })}
      </h1>
      <p className="mb-4 text-sm text-neutral-500">
        {t("knowledgeGraph.subtitle", {
          defaultValue: "提案确认（A/X/P）· 本体树 · 版本 diff · K0 质量面板",
        })}
      </p>
      <Tabs
        activeKey={active}
        onChange={setActive}
        items={[
          {
            key: "queue",
            label: t("knowledgeGraph.tabs.queue", { defaultValue: "提案队列" }),
            children: (
              <ProposalQueue
                onSessionCommitted={(row) => {
                  setCommittedRow(row);
                  setActive("tree");
                }}
              />
            ),
          },
          {
            key: "tree",
            label: t("knowledgeGraph.tabs.tree", { defaultValue: "本体树" }),
            children: <OntologyTreePanel committedRow={committedRow} />,
          },
          {
            key: "diff",
            label: t("knowledgeGraph.tabs.diff", {
              defaultValue: "Diff 与质量",
            }),
            children: <DiffAndQualityPanel />,
          },
        ]}
      />
    </div>
  );
}
