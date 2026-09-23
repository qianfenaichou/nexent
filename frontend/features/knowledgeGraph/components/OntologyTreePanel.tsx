"use client";

// Ontology tree page body: G6 v5 mindmap-style tree built from the active
// snapshot's classes (parent edges), anchor-marked nodes. G6 is a T-05b
// allowed dependency (@antv/g6@5, whitelist 03 plan ss4.1) installed by the
// branch; if it is absent at build time the panel degrades to a plain list.
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Empty, Spin, Tag } from "antd";
import { Graph, treeToGraphData } from "@antv/g6";
import type { GraphData, TreeData } from "@antv/g6";
import type {
  OntologyClassNode,
  OntologyVersionRow,
} from "@/types/knowledgeGraph";
import { ontologyService } from "@/services/knowledgeGraphService";

interface Props {
  // Row returned by the queue's commit (same tick, no refetch). When
  // absent the panel falls back to the read-only active-version endpoint.
  committedRow?: OntologyVersionRow | null;
}

function classesToTree(classes: OntologyClassNode[]): TreeData {
  // Build name -> node index, attach children under their parent; classes
  // whose parent is missing mount at the root (same rule _apply_ops uses).
  const byName = new Map<string, { node: TreeData; parent?: string | null }>();
  for (const c of classes) {
    byName.set(c.name, {
      node: { id: c.name, children: [] },
      parent: c.parent,
    });
  }
  const roots: TreeData[] = [];
  for (const [name, entry] of byName) {
    const parentNode =
      entry.parent && entry.parent !== name
        ? byName.get(entry.parent)
        : undefined;
    if (parentNode) {
      (parentNode.node.children ??= []).push(entry.node);
    } else {
      roots.push(entry.node);
    }
  }
  // Synthetic ROOT anchors the mindmap layout even for a flat forest.
  return { id: "ROOT", children: roots };
}

export function OntologyTreePanel({ committedRow }: Props) {
  const { t } = useTranslation();
  const [versionRow, setVersionRow] = useState<OntologyVersionRow | null>(
    committedRow ?? null
  );
  const [loading, setLoading] = useState(!committedRow);
  // Load failure (network/5xx) must never masquerade as "no data": 404 is
  // already mapped to `null` by the service and renders the Empty state.
  const [loadError, setLoadError] = useState(false);
  const [renderError, setRenderError] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (committedRow) {
      // Fresh commit from the queue session: render it directly.
      setVersionRow(committedRow);
      setLoadError(false);
      setLoading(false);
      return;
    }
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        // Read-only display source; POST /versions stays the only write.
        const row = await ontologyService.activeVersion();
        if (alive) {
          setVersionRow(row);
          setLoadError(false);
        }
      } catch {
        if (alive) {
          setVersionRow(null);
          setLoadError(true);
        }
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [committedRow]);

  // G6 v5 `Graph` only accepts GraphData ({nodes, edges, combos}); nested
  // tree objects must go through the official converter first, otherwise
  // the canvas silently renders 0 nodes / 0 edges.
  const graphData = useMemo<GraphData>(
    () => treeToGraphData(classesToTree(versionRow?.snapshot?.classes ?? [])),
    [versionRow]
  );

  useEffect(() => {
    if (loading || loadError || !containerRef.current) return;
    const classes = versionRow?.snapshot?.classes ?? [];
    if (classes.length === 0) return;
    setRenderError(false);
    try {
      const graph = new Graph({
        container: containerRef.current,
        width: containerRef.current.clientWidth,
        height: 480,
        data: graphData,
        layout: { type: "mindmap", direction: "LR", nodeSep: 12, rankSep: 48 },
        node: {
          style: (datum: { id?: string }) => {
            const cls = classes.find((c) => c.name === datum.id);
            return {
              labelText: String(datum.id ?? ""),
              fill: cls?.deprecated
                ? "#d9d9d9"
                : cls?.anchor
                  ? "#e6f4ff"
                  : "#f0f0f0",
              stroke: cls?.anchor ? "#1677ff" : "#d9d9d9",
            };
          },
        },
        behaviors: ["drag-canvas", "zoom-canvas", "collapse-expand"],
      });
      // render() is async; a rejected render must surface as an error state
      // instead of leaving a silent blank canvas behind.
      void graph.render().catch(() => {
        setRenderError(true);
      });
      return () => {
        void graph.destroy();
      };
    } catch {
      setRenderError(true);
    }
  }, [graphData, loading, loadError, versionRow, t]);

  const classes = versionRow?.snapshot?.classes ?? [];

  return (
    <div className="flex flex-col gap-3">
      {versionRow && (
        <div className="flex items-center gap-2">
          <Tag color="blue">{versionRow.version}</Tag>
          <span className="text-xs text-neutral-500">
            {t("knowledgeGraph.tree.currentVersion", {
              defaultValue: "当前已提交版本",
            })}
          </span>
        </div>
      )}
      {loading ? (
        <div className="flex justify-center py-16">
          <Spin />
        </div>
      ) : loadError ? (
        <Alert
          type="error"
          showIcon
          message={t("knowledgeGraph.tree.loadFailed", {
            defaultValue: "加载本体数据失败，请刷新后重试",
          })}
        />
      ) : classes.length === 0 ? (
        <Empty
          description={t("knowledgeGraph.tree.empty", {
            defaultValue: "尚无已提交本体版本",
          })}
        />
      ) : (
        <>
          {renderError && (
            <Alert
              type="error"
              showIcon
              message={t("knowledgeGraph.tree.renderFailed", {
                defaultValue: "树渲染失败",
              })}
            />
          )}
          <div ref={containerRef} className="rounded-lg border border-border" />
          {!renderError && (
            <p className="text-xs text-neutral-500">
              {t("knowledgeGraph.tree.legend", {
                defaultValue:
                  "蓝框 = 锚定标准条目 · 灰 = 已废弃 · 拖拽/滚轮缩放",
              })}
            </p>
          )}
        </>
      )}
    </div>
  );
}
