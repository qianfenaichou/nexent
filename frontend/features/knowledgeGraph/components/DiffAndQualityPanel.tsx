"use client";

// Version diff replay view: ops between two committed versions with the
// three-color coding frozen in the SPEC (added green / deprecated grey /
// changed yellow). Plus the K0 quality panel (recharts radar, already in
// the dependency tree).
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Empty, Input, Space, Tag } from "antd";
import {
  Radar,
  RadarChart,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  ResponsiveContainer,
} from "recharts";
import type {
  OntologyDiffResult,
  OntologyMetrics,
} from "@/types/knowledgeGraph";
import { ontologyService } from "@/services/knowledgeGraphService";

function opColor(op: string): { color: string; label: string } | null {
  if (op.endsWith("ADD")) return { color: "green", label: "added" };
  if (op.endsWith("DEPRECATE") || op.endsWith("DEL"))
    return { color: "default", label: "deprecated" };
  if (op.endsWith("UPD")) return { color: "gold", label: "changed" };
  return null;
}

export function DiffAndQualityPanel() {
  const { t } = useTranslation();
  const [from, setFrom] = useState("v1.0.0");
  const [to, setTo] = useState("v1.1.0");
  const [diff, setDiff] = useState<OntologyDiffResult | null>(null);
  const [diffError, setDiffError] = useState(false);
  const [metrics, setMetrics] = useState<OntologyMetrics | null>(null);
  const [metricsError, setMetricsError] = useState(false);
  const [metricsVersion, setMetricsVersion] = useState("v1.0.0");

  const runDiff = useCallback(async () => {
    setDiffError(false);
    try {
      setDiff(await ontologyService.diff(from, to));
    } catch {
      setDiff(null);
      setDiffError(true);
    }
  }, [from, to]);

  const loadMetrics = useCallback(async (v: string) => {
    setMetricsError(false);
    try {
      setMetrics(await ontologyService.versionMetrics(v));
    } catch {
      setMetrics(null);
      setMetricsError(true);
    }
  }, []);

  useEffect(() => {
    void runDiff();
  }, [runDiff]);

  useEffect(() => {
    void loadMetrics(metricsVersion);
  }, [metricsVersion, loadMetrics]);

  const radarData = metrics
    ? [
        { axis: "cov", value: metrics.cov },
        { axis: "red", value: metrics.red },
        { axis: "align", value: metrics.align },
        { axis: "dep", value: metrics.dep / 5 }, // normalize depth 1..5
      ]
    : [];

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <div>
        <h3 className="mb-3 font-medium">
          {t("knowledgeGraph.diff.title", { defaultValue: "版本 Diff" })}
        </h3>
        <Space className="mb-3">
          <Input
            size="small"
            style={{ width: 110 }}
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            addonBefore={t("knowledgeGraph.diff.from", { defaultValue: "从" })}
          />
          <Input
            size="small"
            style={{ width: 110 }}
            value={to}
            onChange={(e) => setTo(e.target.value)}
            addonBefore={t("knowledgeGraph.diff.to", { defaultValue: "到" })}
          />
          <Button size="small" type="primary" onClick={() => void runDiff()}>
            {t("knowledgeGraph.diff.run", { defaultValue: "回放" })}
          </Button>
        </Space>
        {diffError ? (
          <Alert
            type="error"
            showIcon
            description={t("knowledgeGraph.diff.loadFailed", {
              defaultValue: "加载版本 Diff 失败，请检查版本号或刷新重试",
            })}
          />
        ) : !diff || diff.ops.length === 0 ? (
          <Empty
            description={t("knowledgeGraph.diff.empty", {
              defaultValue: "无差异操作（版本号有误或未提交）",
            })}
          />
        ) : (
          <ul className="flex max-h-80 flex-col gap-2 overflow-auto pr-1">
            {diff.ops.map((op, i) => {
              const coding = opColor(op.op);
              return (
                <li
                  key={i}
                  className="rounded border border-border px-3 py-2 text-sm"
                >
                  <Tag color={coding?.color ?? "blue"}>{op.op}</Tag>
                  <span className="font-mono">{op.target}</span>
                  {typeof op.payload?.name === "string" && (
                    <span className="ml-2 text-neutral-500">
                      {op.payload.name}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>

      <div>
        <h3 className="mb-3 font-medium">
          {t("knowledgeGraph.quality.title", { defaultValue: "K0 质量面板" })}
        </h3>
        <Space className="mb-3">
          <Input
            size="small"
            style={{ width: 110 }}
            value={metricsVersion}
            onChange={(e) => setMetricsVersion(e.target.value)}
            addonBefore={t("knowledgeGraph.quality.version", {
              defaultValue: "版本",
            })}
          />
        </Space>
        {metricsError ? (
          <Alert
            type="error"
            showIcon
            description={t("knowledgeGraph.quality.loadFailed", {
              defaultValue: "加载质量指标失败，请检查版本或刷新重试",
            })}
          />
        ) : metrics ? (
          <>
            <div className="mb-2 flex flex-wrap gap-2 text-sm">
              <Tag>cov {metrics.cov}</Tag>
              <Tag>red {metrics.red}</Tag>
              <Tag>dep {metrics.dep}</Tag>
              <Tag>align {metrics.align}</Tag>
            </div>
            <div style={{ width: "100%", height: 280 }}>
              <ResponsiveContainer>
                <RadarChart data={radarData} outerRadius="70%">
                  <PolarGrid />
                  <PolarAngleAxis dataKey="axis" />
                  <PolarRadiusAxis domain={[0, 1]} />
                  <Radar
                    dataKey="value"
                    stroke="#1677ff"
                    fill="#1677ff"
                    fillOpacity={0.4}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </>
        ) : (
          <Empty
            description={t("knowledgeGraph.quality.empty", {
              defaultValue: "该版本无指标",
            })}
          />
        )}
      </div>
    </div>
  );
}
