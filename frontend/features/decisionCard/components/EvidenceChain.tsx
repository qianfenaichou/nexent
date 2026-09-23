"use client";

// Expandable evidence chain for one decision-card candidate (T-19).
// Every item carries its provenance (doc / span / kg_path) and the
// EXTRACTED|INFERRED tag - the traceability the card contract promises.
// Contested items are marked, never silently dropped.
// The source line never renders a bare "None": a row whose doc/span did not
// resolve degrades to a label ("图谱内证据" / "—") via evidenceSourceDisplay.
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Badge, Collapse, Tag, Typography } from "antd";
import {
  evidenceSourceDisplay,
  type DecisionCardCandidate,
  type DecisionCardEvidenceItem,
} from "@/types/decisionCard";

const { Text } = Typography;

function tagColor(tag: string): string {
  if (tag === "EXTRACTED") return "green";
  if (tag === "INFERRED") return "orange";
  return "default";
}

function EvidenceItemView({ item }: { item: DecisionCardEvidenceItem }) {
  const { t } = useTranslation();
  const source = evidenceSourceDisplay(item.provenance, item.source_channel);
  const sourceLabel =
    source.kind === "fields"
      ? [source.doc, source.span].filter(Boolean).join(" · ")
      : source.kind === "graph"
        ? t("decisionCard.evidence.graphSource", { defaultValue: "图谱内证据" })
        : t("decisionCard.evidence.sourceMissing", { defaultValue: "—" });
  return (
    <div className="flex flex-col gap-1 py-1">
      <div className="flex flex-wrap items-center gap-1">
        <Tag color={tagColor(item.tag)}>{item.tag}</Tag>
        <Tag>{item.source_channel}</Tag>
        {item.contested ? (
          <Tag color="red">
            {t("decisionCard.evidence.contested", {
              defaultValue: "知识不一致",
            })}
          </Tag>
        ) : null}
        {item.provenance.version_pinned ? (
          <Tag color="blue">
            {t("decisionCard.evidence.versionPinned", {
              defaultValue: "版本钉住",
            })}
          </Tag>
        ) : null}
      </div>
      <Text className="text-sm">{item.claim}</Text>
      <Text className="text-xs text-neutral-500">{sourceLabel}</Text>
      {item.provenance.kg_path?.length ? (
        <Text className="text-xs text-neutral-400" code>
          {item.provenance.kg_path.join(" -> ")}
        </Text>
      ) : null}
    </div>
  );
}

export function EvidenceChain({
  candidate,
}: {
  candidate: DecisionCardCandidate;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const items = candidate.evidence_chain ?? [];
  if (!items.length) {
    return (
      <Text className="text-xs text-neutral-400">
        {t("decisionCard.evidence.empty", { defaultValue: "无证据链" })}
      </Text>
    );
  }
  const contested = items.some((i) => i.contested);
  return (
    <Collapse
      size="small"
      activeKey={open ? ["chain"] : []}
      onChange={(keys) =>
        setOpen(Array.isArray(keys) ? keys.includes("chain") : keys === "chain")
      }
      items={[
        {
          key: "chain",
          label: (
            <span className="flex items-center gap-2">
              {t("decisionCard.evidence.title", { defaultValue: "证据链" })}
              <Badge count={items.length} color="blue" />
              {contested ? (
                <Tag color="red">
                  {t("decisionCard.evidence.contested", {
                    defaultValue: "知识不一致",
                  })}
                </Tag>
              ) : null}
            </span>
          ),
          children: (
            <div className="divide-y divide-neutral-100">
              {items.map((item, idx) => (
                <EvidenceItemView key={idx} item={item} />
              ))}
            </div>
          ),
        },
      ]}
    />
  );
}
