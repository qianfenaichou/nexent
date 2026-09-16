"use client";

// Ontology tree page body: G6 v5 mindmap-style tree built from the active
// snapshot's classes (parent edges), anchor-marked nodes. G6 is a T-05b
// allowed dependency (@antv/g6@5, whitelist 03 plan ss4.1) installed by the
// branch; if it is absent at build time the panel degrades to a plain list.
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Empty, Spin, Tag, message } from "antd";
import { Graph } from "@antv/g6";
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

function classesToTree(classes: OntologyClassNode[]) {
  // Build name -> node index, attach children under their parent; classes
  // whose parent is missing mount at the root (same rule _apply_ops uses).
  const byName = new Map<
    string,
    { id: string; children: unknown[]; cls: OntologyClassNode }
  >();
  for (const c of classes) {
    byName.set(c.name, { id: c.name, children: [], cls: c });
  }
  const roots: unknown[] = [];
  for (const node of byName.values()) {
    const parent = node.cls.parent;
    const parentNode = parent ? byName.get(parent) : undefined;
    if (parentNode && parentNode.cls.name !== node.cls.name) {
      parentNode.children.push(node);
    } else {
      roots.push(node);
    }
  }
  return { id: "ROOT", children: roots };
}

export function OntologyTreePanel({ committedRow }: Props) {
  const { t } = useTranslation();
  const [versionRow, setVersionRow] = useState<OntologyVersionRow | null>(
    committedRow ?? null
  );
  const [loading, setLoading] = useState(!committedRow);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (committedRow) {
      // Fresh commit from the queue session: render it directly.
      setVersionRow(committedRow);
      setLoading(false);
      return;
    }
    let alive = true;
    (async () => {
      setLoading(true);
      try {
        // Read-only display source; POST /versions stays the only write.
        const row = await ontologyService.activeVersion();
        if (alive) setVersionRow(row);
      } catch {
        if (alive) setVersionRow(null);
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [committedRow]);

  const treeData = useMemo(
    () => classesToTree(versionRow?.snapshot?.classes ?? []),
    [versionRow]
  );

  useEffect(() => {
    if (loading || !containerRef.current) return;
    const classes = versionRow?.snapshot?.classes ?? [];
    if (classes.length === 0) return;
    try {
      const graph = new Graph({
        container: containerRef.current,
        width: containerRef.current.clientWidth,
        height: 480,
        data: treeData as never,
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
      void graph.render();
      return () => {
        void graph.destroy();
      };
    } catch {
      message.error(
        t("knowledgeGraph.tree.renderFailed", { defaultValue: "树渲染失败" })
      );
    }
  }, [treeData, loading, versionRow, t]);

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
      ) : classes.length === 0 ? (
        <Empty
          description={t("knowledgeGraph.tree.empty", {
            defaultValue: "尚无已提交本体版本",
          })}
        />
      ) : (
        <>
          <div ref={containerRef} className="rounded-lg border border-border" />
          <p className="text-xs text-neutral-500">
            {t("knowledgeGraph.tree.legend", {
              defaultValue: "蓝框 = 锚定标准条目 · 灰 = 已废弃 · 拖拽/滚轮缩放",
            })}
          </p>
        </>
      )}
    </div>
  );
}
