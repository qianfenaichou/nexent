"use client";

// Node-level version comparison + three-color op list (added green /
// deprecated grey / changed gold - same paint as DiffAndQualityPanel).
// Data source is the WIRED GET /api/knowevo/ontology/diff?from&to plus
// version metrics for the pair. Selecting a node pins its ops and shows
// payload keys so the compare is node-scoped rather than a flat dump.
import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Input,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from "antd";
import type {
  OntologyDiffResult,
  OntologyMetrics,
} from "@/types/knowledgeGraph";
import type { DescriptionsProps } from "antd";
import { evolutionBoardService } from "../service";
import { toDiffOpView } from "../diffColor";
import type { DiffOpView } from "../types";

const { Text, Title } = Typography;

type DescriptionItem = NonNullable<DescriptionsProps["items"]>[number];

function payloadPairs(payload: Record<string, unknown>): [string, unknown][] {
  return Object.entries(payload).filter(([, v]) => v !== undefined);
}

function metricValue(value: number | undefined | null): string {
  return value === undefined || value === null
    ? "-"
    : Number(value).toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

// `key` is passed separately from `label`: the labels embed the two version
// strings, so comparing a version with itself would otherwise repeat a key.
function metricItem(
  key: string,
  label: string,
  value: number | undefined | null
): DescriptionItem {
  return { key, label, children: metricValue(value) };
}

export function NodeVersionDiff() {
  const { t } = useTranslation();
  const [from, setFrom] = useState("v1.0.0");
  const [to, setTo] = useState("v1.1.0");
  const [diff, setDiff] = useState<OntologyDiffResult | null>(null);
  const [metrics, setMetrics] = useState<{
    from: OntologyMetrics | null;
    to: OntologyMetrics | null;
  }>({ from: null, to: null });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [nodeFilter, setNodeFilter] = useState("");
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const runDiff = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const [ops, mFrom, mTo] = await Promise.all([
        evolutionBoardService.ontologyDiff(from, to),
        evolutionBoardService.versionMetrics(from).catch(() => null),
        evolutionBoardService.versionMetrics(to).catch(() => null),
      ]);
      setDiff(ops);
      setMetrics({ from: mFrom, to: mTo });
      setSelectedNode(null);
    } catch {
      setDiff(null);
      setMetrics({ from: null, to: null });
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [from, to]);

  useEffect(() => {
    void runDiff();
  }, [runDiff]);

  const ops: DiffOpView[] = useMemo(
    () => (diff?.ops ?? []).map(toDiffOpView),
    [diff]
  );

  const nodeNames = useMemo(() => {
    const set = new Set<string>();
    for (const op of ops) set.add(op.target);
    return Array.from(set).sort();
  }, [ops]);

  const visibleOps = useMemo(() => {
    let list = ops;
    if (nodeFilter.trim()) {
      const q = nodeFilter.trim().toLowerCase();
      list = list.filter(
        (op) =>
          op.target.toLowerCase().includes(q) || op.op.toLowerCase().includes(q)
      );
    }
    if (selectedNode) {
      list = list.filter((op) => op.target === selectedNode);
    }
    return list;
  }, [ops, nodeFilter, selectedNode]);

  const copyOps = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(visibleOps, null, 2));
      message.success(
        t("evolutionBoard.nodeDiff.copied", { defaultValue: "已复制 ops JSON" })
      );
    } catch {
      message.error(
        t("evolutionBoard.nodeDiff.copyFailed", { defaultValue: "复制失败" })
      );
    }
  };

  return (
    <section className="rounded-lg border border-neutral-200 p-4">
      <Title level={5} className="!mb-1">
        {t("evolutionBoard.nodeDiff.title", {
          defaultValue: "节点级版本对比（三色 diff）",
        })}
      </Title>
      <Text type="secondary" className="mb-3 block text-xs">
        {t("evolutionBoard.nodeDiff.subtitle", {
          defaultValue:
            "绿=新增 / 灰=废弃 / 金=变更。来源 GET /api/knowevo/ontology/diff。点选节点只看该节点的 op 与 payload。",
        })}
      </Text>

      <Space wrap className="mb-3">
        <Space.Compact>
          <Space.Addon>
            {t("evolutionBoard.nodeDiff.from", { defaultValue: "from" })}
          </Space.Addon>
          <Input
            value={from}
            onChange={(e) => setFrom(e.target.value)}
            style={{ width: 160 }}
            aria-label={t("evolutionBoard.nodeDiff.from", {
              defaultValue: "from",
            })}
          />
        </Space.Compact>
        <Space.Compact>
          <Space.Addon>
            {t("evolutionBoard.nodeDiff.to", { defaultValue: "to" })}
          </Space.Addon>
          <Input
            value={to}
            onChange={(e) => setTo(e.target.value)}
            style={{ width: 160 }}
            aria-label={t("evolutionBoard.nodeDiff.to", {
              defaultValue: "to",
            })}
          />
        </Space.Compact>
        <Button type="primary" onClick={() => void runDiff()} loading={loading}>
          {t("evolutionBoard.nodeDiff.run", { defaultValue: "对比" })}
        </Button>
        <Input
          placeholder={t("evolutionBoard.nodeDiff.filter", {
            defaultValue: "按节点/op 过滤",
          })}
          value={nodeFilter}
          onChange={(e) => setNodeFilter(e.target.value)}
          allowClear
          style={{ width: 200 }}
        />
        <Button size="small" onClick={() => void copyOps()}>
          {t("evolutionBoard.nodeDiff.copy", { defaultValue: "复制 ops" })}
        </Button>
        {selectedNode ? (
          <Button size="small" onClick={() => setSelectedNode(null)}>
            {t("evolutionBoard.nodeDiff.clearNode", {
              defaultValue: "清除节点选择",
            })}
          </Button>
        ) : null}
      </Space>

      <Descriptions
        size="small"
        column={4}
        className="mb-3"
        items={[
          metricItem("from-cov", `${from} cov`, metrics.from?.cov),
          metricItem("to-cov", `${to} cov`, metrics.to?.cov),
          metricItem("from-red", `${from} red`, metrics.from?.red),
          metricItem("to-red", `${to} red`, metrics.to?.red),
          metricItem("from-dep", `${from} dep`, metrics.from?.dep),
          metricItem("to-dep", `${to} dep`, metrics.to?.dep),
          metricItem("from-align", `${from} align`, metrics.from?.align),
          metricItem("to-align", `${to} align`, metrics.to?.align),
        ]}
      />

      {error ? (
        <Alert
          type="error"
          showIcon
          title={t("evolutionBoard.nodeDiff.error", {
            defaultValue: "版本 diff 请求失败，可重试",
          })}
          action={
            <Button size="small" onClick={() => void runDiff()}>
              {t("common.retry", { defaultValue: "重试" })}
            </Button>
          }
        />
      ) : loading ? (
        <div className="flex justify-center py-8">
          <Spin />
        </div>
      ) : visibleOps.length === 0 ? (
        <Empty
          description={t("evolutionBoard.nodeDiff.empty", {
            defaultValue: "该版本区间无节点变更，或过滤条件未命中",
          })}
        />
      ) : (
        <div className="flex flex-col gap-3">
          {nodeNames.length > 0 ? (
            <Space size={4} wrap>
              {nodeNames.slice(0, 24).map((name) => (
                <Tag.CheckableTag
                  key={name}
                  checked={selectedNode === name}
                  onChange={(checked) => setSelectedNode(checked ? name : null)}
                >
                  {name}
                </Tag.CheckableTag>
              ))}
            </Space>
          ) : null}

          <ul className="m-0 list-none space-y-2 p-0">
            {visibleOps.map((op, idx) => (
              <li
                key={`${op.op}:${op.target}:${idx}`}
                className="rounded border border-neutral-100 p-2"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Tag color={op.color}>{op.label}</Tag>
                  <Text code className="text-xs">
                    {op.op}
                  </Text>
                  <Text strong>{op.target}</Text>
                  <Button
                    size="small"
                    type="link"
                    onClick={() => setSelectedNode(op.target)}
                  >
                    {t("evolutionBoard.nodeDiff.focusNode", {
                      defaultValue: "只看此节点",
                    })}
                  </Button>
                </div>
                {payloadPairs(op.payload).length > 0 ? (
                  <div className="mt-2 grid gap-1 sm:grid-cols-2">
                    {payloadPairs(op.payload).map(([key, value]) => (
                      <div key={key} className="text-xs">
                        <Text type="secondary">{key}: </Text>
                        <Text className="break-all">
                          {typeof value === "string"
                            ? value
                            : JSON.stringify(value)}
                        </Text>
                      </div>
                    ))}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
