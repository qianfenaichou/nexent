"use client";

// Compact decision-card renderer for the chat stream (T-19): mounted by
// the `case "data"` branch in thread.tsx when a part with
// name="decision-card" arrives. The full-featured surface is the
// /decisionCard panel; this inline card keeps the same sections visible
// in miniature (candidates, evidence count, version stamp, disclaimer).
import { useTranslation } from "react-i18next";
import { Alert, Tag, Typography } from "antd";
import type { DecisionCardPayload } from "@/types/decisionCard";

const { Text } = Typography;

export function DecisionCardMessage({ card }: { card: DecisionCardPayload }) {
  const { t } = useTranslation();
  if (!card || typeof card !== "object" || !card.question) {
    return null;
  }
  const insufficient =
    card.decision === "INSUFFICIENT_EVIDENCE" || !card.candidates?.length;
  return (
    <div
      data-slot="knowevo-decision-card"
      className="my-2 rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-700 dark:bg-neutral-900"
    >
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <Tag color={insufficient ? "warning" : "processing"}>
          {insufficient
            ? t("decisionCard.decision.insufficient", {
                defaultValue: "证据不足",
              })
            : t("decisionCard.decision.recommend", {
                defaultValue: "决策建议",
              })}
        </Tag>
        {card.knowledge_stamp?.ontology_version ? (
          <Tag color="blue">
            {t("decisionCard.stamp.version", { defaultValue: "知识版本" })}
            {card.knowledge_stamp.ontology_version}
          </Tag>
        ) : null}
        {card.knowledge_version_pinned ? (
          <Tag color="geekblue">
            {t("decisionCard.evidence.versionPinned", {
              defaultValue: "版本钉住",
            })}
          </Tag>
        ) : null}
      </div>
      <Text className="text-sm font-medium">{card.question}</Text>
      {insufficient ? (
        <Alert
          className="mt-2"
          type="warning"
          showIcon
          message={t("decisionCard.decision.insufficientNote", {
            defaultValue: "证据不足，按纪律不生成候选。",
          })}
        />
      ) : (
        <ul className="mt-2 flex flex-col gap-1">
          {card.candidates.map((cand, idx) => (
            <li key={idx} className="text-sm">
              <span className="font-medium">{cand.option}</span>
              <span className="ml-2 text-xs text-neutral-500">
                {t("decisionCard.candidate.confidence", {
                  defaultValue: "置信度",
                })}{" "}
                {(cand.confidence_calibrated ?? 0).toFixed(2)}
              </span>
              {cand.evidence_chain?.length ? (
                <span className="ml-2 text-xs text-neutral-400">
                  {t("decisionCard.evidence.count", {
                    defaultValue: "{{count}} 条证据",
                    count: cand.evidence_chain.length,
                  })}
                </span>
              ) : null}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 border-t border-neutral-100 pt-1 dark:border-neutral-800">
        <Text className="text-xs text-neutral-400">
          {card.disclaimer ||
            t("decisionCard.disclaimer", {
              defaultValue: "本系统提供认知辅助，不构成处方建议",
            })}
        </Text>
      </div>
    </div>
  );
}
