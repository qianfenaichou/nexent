"use client";

// Evolution timeline: document-version events (wired alignment/diff/list)
// stacked above the evolution-round ledger rows when that HTTP route lands.
// Honesty (workorder D6):
//   roundsFailure=pending_wiring -> Alert (route missing), not an empty list
//   rounds=[] and no failure     -> empty state
//   roundsFailure=forbidden      -> 403 tenant/RBAC notice
// Never invent round rows.
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Empty,
  Segmented,
  Tag,
  Timeline,
  Typography,
} from "antd";
import type { AlignmentDiffRow, EvolutionRoundSummary } from "../types";
import type { LoadFailureKind } from "../errorClass";
import { timelineStatusColor } from "../opsSummary";

const { Text } = Typography;

type SourceFilter = "all" | "doc" | "round";

function changeCountTags(counts?: Record<string, number>) {
  const entries = Object.entries(counts ?? {}).filter(([, n]) => n > 0);
  if (entries.length === 0) return null;
  return entries.map(([kind, n]) => (
    <Tag key={kind} className="mr-1">
      {kind}: {n}
    </Tag>
  ));
}

function RoundsFailureAlert({
  kind,
  onRetry,
}: {
  kind: LoadFailureKind;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  if (kind === "pending_wiring") {
    return (
      <Alert
        className="mb-3"
        type="warning"
        showIcon
        message={t("evolutionBoard.timeline.roundsPending.title", {
          defaultValue: "演进轮次台账接口暂不可用",
        })}
        description={t("evolutionBoard.timeline.roundsPending.body", {
          defaultValue:
            "GET /api/knowevo/evolution/timeline 返回 404/405（接线后语义=路由缺失或不可用，不是空数据）。本区块不编造轮次；文档版本 diff 时间轴不受影响。",
        })}
        action={
          onRetry ? (
            <Button size="small" onClick={onRetry}>
              {t("common.retry", { defaultValue: "重试" })}
            </Button>
          ) : null
        }
      />
    );
  }
  if (kind === "forbidden") {
    return (
      <Alert
        className="mb-3"
        type="error"
        showIcon
        message={t("evolutionBoard.timeline.roundsForbidden.title", {
          defaultValue: "无权读取演进轮次",
        })}
        description={t("evolutionBoard.timeline.roundsForbidden.body", {
          defaultValue:
            "服务返回 403：当前会话租户/RBAC 不允许读取该演进台账（工单 W5 跨租户闸）。不是空列表，请勿当成无数据。",
        })}
      />
    );
  }
  return (
    <Alert
      className="mb-3"
      type="error"
      showIcon
      message={t("evolutionBoard.timeline.roundsError.title", {
        defaultValue: "演进轮次加载失败",
      })}
      description={t("evolutionBoard.timeline.roundsError.body", {
        defaultValue:
          "请求未成功（网络或服务端错误）。不展示虚构轮次；下方文档 diff 不受影响。",
      })}
      action={
        onRetry ? (
          <Button size="small" onClick={onRetry}>
            {t("common.retry", { defaultValue: "重试" })}
          </Button>
        ) : null
      }
    />
  );
}

export function EvolutionTimeline({
  alignmentDiffs,
  rounds,
  roundsFailure,
  selectedId,
  onSelect,
  onRetryRounds,
}: {
  alignmentDiffs: AlignmentDiffRow[];
  /** null = not loaded (see roundsFailure); [] = loaded and empty. */
  rounds: EvolutionRoundSummary[] | null;
  roundsFailure: LoadFailureKind | null;
  selectedId?: string | null;
  onSelect?: (id: string, kind: "diff" | "round") => void;
  onRetryRounds?: () => void;
}) {
  const { t } = useTranslation();
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all");

  const items = useMemo(() => {
    const docItems =
      sourceFilter === "round"
        ? []
        : alignmentDiffs.map((row) => ({
            key: `diff:${row.diff_id}`,
            color: "blue" as const,
            children: (
              <button
                type="button"
                aria-current={selectedId === row.diff_id ? "true" : undefined}
                className={`w-full cursor-pointer border-0 bg-transparent p-0 text-left ${
                  selectedId === row.diff_id ? "opacity-100" : "opacity-90"
                }`}
                onClick={() => onSelect?.(row.diff_id, "diff")}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Text strong>
                    {row.old_asset_no ?? "?"} → {row.new_asset_no ?? "?"}
                  </Text>
                  <Tag color="blue">
                    {t("evolutionBoard.timeline.docDiff", {
                      defaultValue: "文档版本 diff",
                    })}
                  </Tag>
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  {row.created_at ?? "-"}
                </div>
                <div className="mt-1">{changeCountTags(row.change_counts)}</div>
              </button>
            ),
          }));

    const roundItems =
      sourceFilter === "doc"
        ? []
        : (rounds ?? []).map((row) => ({
            key: `round:${row.round_id}`,
            color: timelineStatusColor(row.status),
            children: (
              <button
                type="button"
                aria-current={selectedId === row.round_id ? "true" : undefined}
                className="w-full cursor-pointer border-0 bg-transparent p-0 text-left"
                onClick={() => onSelect?.(row.round_id, "round")}
              >
                <div className="flex flex-wrap items-center gap-2">
                  <Text strong>{row.trigger_source || row.round_id}</Text>
                  <Tag>{row.status}</Tag>
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  {row.at ?? "-"} · {row.round_id}
                </div>
              </button>
            ),
          }));

    return [...docItems, ...roundItems];
  }, [alignmentDiffs, rounds, sourceFilter, selectedId, onSelect, t]);

  // Empty copy depends on *why* the list is short (D6).
  const emptyDescription = roundsFailure
    ? t("evolutionBoard.timeline.emptyBlocked", {
        defaultValue:
          "演进轮次暂不可用（见上方说明）。若也无文档 diff，请先跑 alignment diff。",
      })
    : t("evolutionBoard.timeline.empty", {
        defaultValue:
          "暂无演进事件：先跑 alignment diff 或启动一轮 evolution round",
      });

  return (
    <section className="rounded-lg border border-neutral-200 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <Text strong>
          {t("evolutionBoard.timeline.title", { defaultValue: "演进时间轴" })}
        </Text>
        <Segmented
          size="small"
          value={sourceFilter}
          onChange={(v) => setSourceFilter(v as SourceFilter)}
          options={[
            {
              label: t("evolutionBoard.timeline.filter.all", {
                defaultValue: "全部",
              }),
              value: "all",
            },
            {
              label: t("evolutionBoard.timeline.filter.doc", {
                defaultValue: "文档 diff",
              }),
              value: "doc",
            },
            {
              label: t("evolutionBoard.timeline.filter.round", {
                defaultValue: "演进轮次",
              }),
              value: "round",
            },
          ]}
        />
      </div>
      <Text type="secondary" className="mb-3 block text-xs">
        {t("evolutionBoard.timeline.subtitle", {
          defaultValue:
            "文档版本 diff（GET /api/knowevo/alignment/diff/list，已接线）；演进轮次台账（GET /api/knowevo/evolution/timeline，异常分型 Alert，有数据才列出）。",
        })}
      </Text>

      {roundsFailure ? (
        <RoundsFailureAlert kind={roundsFailure} onRetry={onRetryRounds} />
      ) : null}

      {items.length === 0 ? (
        <Empty description={emptyDescription} />
      ) : (
        <Timeline items={items} />
      )}
    </section>
  );
}
