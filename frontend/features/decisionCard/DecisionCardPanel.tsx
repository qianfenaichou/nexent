"use client";

// Decision-card panel (T-19): question in -> card out. Route:
// /decisionCard. All contract sections stay visible: candidates with
// expandable evidence chains, EXTRACTED/INFERRED tags, the knowledge
// version stamp with the version_pinned flag, conflict adjudications,
// uncertainty notes, and the healthcare disclaimer which is permanent
// (02-tech-plan 3.3 medical boundary).
import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import type {
  DecisionCardMode,
  DecisionCardPayload,
} from "@/types/decisionCard";
import { decisionCardService } from "@/services/decisionCardService";
import { EvidenceChain } from "./components/EvidenceChain";

const { Text, Title } = Typography;

function CandidateCard({
  candidate,
  topRank,
}: {
  candidate: DecisionCardPayload["candidates"][number];
  topRank: boolean;
}) {
  const { t } = useTranslation();
  return (
    <Card size="small" className="w-full">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Text strong>{candidate.option}</Text>
        {topRank ? (
          <Tag color="processing">
            {t("decisionCard.candidate.top", { defaultValue: "Top-1" })}
          </Tag>
        ) : null}
        <Tag>
          {t("decisionCard.candidate.confidence", { defaultValue: "置信度" })}{" "}
          {(candidate.confidence_calibrated ?? 0).toFixed(2)}
        </Tag>
        {candidate.counterfactual ? (
          <Tag color="orange">
            {t("decisionCard.candidate.counterfactual", {
              defaultValue: "反事实",
            })}
            {candidate.counterfactual.not_choose}
          </Tag>
        ) : null}
      </div>
      <EvidenceChain candidate={candidate} />
      {candidate.risks?.length ? (
        <Alert
          className="mt-2"
          type="warning"
          showIcon
          message={t("decisionCard.candidate.risks", { defaultValue: "风险" })}
          description={
            <ul className="m-0 list-disc pl-4">
              {candidate.risks.map((risk, idx) => (
                <li key={idx}>{risk}</li>
              ))}
            </ul>
          }
        />
      ) : null}
    </Card>
  );
}

export default function DecisionCardPanel() {
  const { t } = useTranslation();
  const [question, setQuestion] = useState("");
  const [version, setVersion] = useState("");
  const [asOf, setAsOf] = useState("");
  const [mode, setMode] = useState<DecisionCardMode>("full");
  const [loading, setLoading] = useState(false);
  const [card, setCard] = useState<DecisionCardPayload | null>(null);

  const render = async () => {
    const q = question.trim();
    if (!q) return;
    setLoading(true);
    try {
      const result = await decisionCardService.renderCard({
        question: q,
        ontologyVersion: version.trim() || undefined,
        asOf: asOf.trim() || undefined,
        mode,
      });
      setCard(result);
    } catch {
      message.error(
        t("decisionCard.renderFailed", { defaultValue: "决策卡生成失败" })
      );
    } finally {
      setLoading(false);
    }
  };

  const insufficient =
    card &&
    (card.decision === "INSUFFICIENT_EVIDENCE" || !card.candidates?.length);

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4 px-4 py-6">
      <div>
        <Title level={4} className="!mb-1">
          {t("decisionCard.title", { defaultValue: "决策卡" })}
        </Title>
        <Text className="text-sm text-neutral-500">
          {t("decisionCard.subtitle", {
            defaultValue:
              "输入问题，基于知识图谱证据链生成结构化决策卡（候选 / 证据 / 置信度 / 风险 / 冲突裁决）",
          })}
        </Text>
      </div>

      <Card size="small">
        <Space direction="vertical" className="w-full" size={8}>
          <Input.TextArea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={t("decisionCard.questionPlaceholder", {
              defaultValue: "例如：eGFR 45 的 2 型糖尿病患者如何选药？",
            })}
            autoSize={{ minRows: 2, maxRows: 4 }}
            maxLength={500}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={version}
              onChange={(e) => setVersion(e.target.value)}
              placeholder={t("decisionCard.versionPlaceholder", {
                defaultValue: "知识版本（可选，如 v1.0.0）",
              })}
              className="w-56"
              allowClear
            />
            {/* Business time (as_of): pins the fact clock, never the version. */}
            <Input
              type="datetime-local"
              value={asOf}
              onChange={(e) => setAsOf(e.target.value)}
              placeholder={t("decisionCard.asOfPlaceholder", {
                defaultValue: "业务时间（可选）",
              })}
              className="w-56"
              allowClear
            />
            {asOf ? (
              <Button size="small" onClick={() => setAsOf("")}>
                {t("decisionCard.asOfClear", { defaultValue: "清除" })}
              </Button>
            ) : null}
            <Button
              type={mode === "full" ? "primary" : "default"}
              size="small"
              onClick={() => setMode("full")}
            >
              {t("decisionCard.mode.full", { defaultValue: "完整卡" })}
            </Button>
            <Button
              type={mode === "lite" ? "primary" : "default"}
              size="small"
              onClick={() => setMode("lite")}
            >
              {t("decisionCard.mode.lite", { defaultValue: "精简卡" })}
            </Button>
            <Button
              type="primary"
              loading={loading}
              onClick={render}
              disabled={!question.trim()}
            >
              {t("decisionCard.render", { defaultValue: "生成决策卡" })}
            </Button>
          </div>
          <Text className="text-xs text-neutral-500">
            {t("decisionCard.asOfHint", {
              defaultValue:
                "钉住事实时钟（as_of）：仅移动事实自身的时间轴；未填知识版本时不产生版本约束",
            })}
          </Text>
        </Space>
      </Card>

      {/* Healthcare disclaimer: permanent on this page (02 §3.3). */}
      <Alert
        type="info"
        showIcon
        message={t("decisionCard.disclaimer", {
          defaultValue: "本系统提供认知辅助，不构成处方建议",
        })}
      />

      {loading ? (
        <div className="flex justify-center py-10">
          <Spin />
        </div>
      ) : card ? (
        <Card className="w-full">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <Tag color={insufficient ? "warning" : "processing"}>
              {insufficient
                ? t("decisionCard.decision.insufficient", {
                    defaultValue: "证据不足",
                  })
                : t("decisionCard.decision.recommend", {
                    defaultValue: "建议",
                  })}
            </Tag>
            {card.knowledge_stamp?.ontology_version ? (
              <Tag color="blue">
                {t("decisionCard.stamp.version", {
                  defaultValue: "知识版本",
                })}
                : {card.knowledge_stamp.ontology_version}
              </Tag>
            ) : null}
            {card.knowledge_stamp?.kg_cutoff ? (
              <Tag>
                {t("decisionCard.stamp.cutoff", { defaultValue: "截止" })}{" "}
                {card.knowledge_stamp.kg_cutoff}
              </Tag>
            ) : null}
            {card.knowledge_stamp?.clock_source ? (
              <Tag>
                {t("decisionCard.stamp.clock", { defaultValue: "时钟" })}{" "}
                {card.knowledge_stamp.clock_source}
              </Tag>
            ) : null}
            {card.knowledge_version_pinned ? (
              <Tag color="geekblue">
                {t("decisionCard.stamp.pinned", {
                  defaultValue: "version_pinned",
                })}
              </Tag>
            ) : null}
            <Tag>
              {t("decisionCard.stamp.calibration", { defaultValue: "校准" })}{" "}
              {card.calibration_applied
                ? t("decisionCard.stamp.calibrationApplied", {
                    defaultValue: "已应用",
                  })
                : t("decisionCard.stamp.calibrationNone", {
                    defaultValue: "未应用",
                  })}
            </Tag>
          </div>
          <Text className="text-sm">{card.question}</Text>

          {insufficient ? (
            <Alert
              className="mt-3"
              type="warning"
              showIcon
              message={t("decisionCard.decision.insufficient", {
                defaultValue: "证据不足",
              })}
              description={card.uncertainty_notes?.join("；") || undefined}
            />
          ) : (
            <div className="mt-3 flex flex-col gap-3">
              {card.candidates.map((cand, idx) => (
                <CandidateCard key={idx} candidate={cand} topRank={idx === 0} />
              ))}
            </div>
          )}

          {card.conflict_adjudications?.length ? (
            <div className="mt-4">
              <Text strong className="text-sm">
                {t("decisionCard.conflicts.title", {
                  defaultValue: "冲突裁决",
                })}
              </Text>
              <ul className="mt-1 flex list-disc flex-col gap-1 pl-5">
                {card.conflict_adjudications.map((adj, idx) => (
                  <li key={idx} className="text-sm">
                    <Tag color="red">{adj.type}</Tag>
                    {adj.resolution}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {card.uncertainty_notes?.length && !insufficient ? (
            <Alert
              className="mt-4"
              type="warning"
              showIcon
              message={t("decisionCard.uncertainty", {
                defaultValue: "不确定性说明",
              })}
              description={
                <ul className="m-0 list-disc pl-4">
                  {card.uncertainty_notes.map((note, idx) => (
                    <li key={idx}>{note}</li>
                  ))}
                </ul>
              }
            />
          ) : null}
        </Card>
      ) : (
        <Empty
          description={t("decisionCard.empty", {
            defaultValue: "尚未生成决策卡",
          })}
        />
      )}
    </div>
  );
}
