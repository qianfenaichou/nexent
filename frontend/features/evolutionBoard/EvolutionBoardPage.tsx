"use client";

// Evolution board (L10): timeline + three-color node-level version compare.
// Route (pending wiring): /evolutionBoard.
// Honesty (workorder D6 / red line #6):
//   - no HTTP route for rounds  -> Alert=pending-wiring (never fake rows)
//   - route OK + empty list     -> empty state
//   - 403                       -> forbidden (tenant / RBAC), not "empty"
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Alert, Button, Typography } from "antd";
import { evolutionBoardService } from "./service";
import type { LoadFailureKind } from "./errorClass";
import type {
  AlignmentDiffRow,
  EvolutionRoundReport,
  EvolutionRoundSummary,
} from "./types";
import { EvolutionTimeline } from "./components/EvolutionTimeline";
import { RoundDetailPanel } from "./components/RoundDetailPanel";
import { AlignmentDiffDetail } from "./components/AlignmentDiffDetail";
import { NodeVersionDiff } from "./components/NodeVersionDiff";

const { Text, Title } = Typography;

export default function EvolutionBoardPage() {
  const { t } = useTranslation();
  const [alignmentDiffs, setAlignmentDiffs] = useState<AlignmentDiffRow[]>([]);
  const [alignmentError, setAlignmentError] = useState(false);
  const [rounds, setRounds] = useState<EvolutionRoundSummary[] | null>(null);
  const [roundsFailure, setRoundsFailure] = useState<LoadFailureKind | null>(
    null
  );
  const [selectedRound, setSelectedRound] =
    useState<EvolutionRoundReport | null>(null);
  const [selectedDiff, setSelectedDiff] = useState<AlignmentDiffRow | null>(
    null
  );
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailFailure, setDetailFailure] = useState<LoadFailureKind | null>(
    null
  );
  const [detailRoundId, setDetailRoundId] = useState<string | null>(null);

  const loadAlignment = useCallback(() => {
    let cancelled = false;
    setAlignmentError(false);
    evolutionBoardService
      .listAlignmentDiffs()
      .then((rows) => {
        if (!cancelled) setAlignmentDiffs(rows);
      })
      .catch(() => {
        if (!cancelled) {
          setAlignmentDiffs([]);
          setAlignmentError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadRounds = useCallback(() => {
    let cancelled = false;
    setRoundsFailure(null);
    evolutionBoardService.listRounds().then((result) => {
      if (cancelled) return;
      if (result.ok) {
        setRounds(result.rows);
        setRoundsFailure(null);
      } else {
        // Distinguish empty vs not-wired vs forbidden - never fake rows.
        setRounds(null);
        setRoundsFailure(result.kind);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const c1 = loadAlignment();
    const c2 = loadRounds();
    return () => {
      c1();
      c2();
    };
  }, [loadAlignment, loadRounds]);

  const loadRoundDetail = useCallback((id: string) => {
    setDetailRoundId(id);
    setDetailLoading(true);
    setDetailFailure(null);
    evolutionBoardService.roundDetail(id).then((result) => {
      if (result.ok) {
        setSelectedRound(result.report);
        setDetailFailure(null);
      } else {
        setSelectedRound(null);
        setDetailFailure(result.kind);
      }
      setDetailLoading(false);
    });
  }, []);

  const onSelect = useCallback(
    (id: string, kind: "diff" | "round") => {
      if (kind === "diff") {
        setSelectedRound(null);
        setDetailRoundId(null);
        setDetailFailure(null);
        setSelectedDiff(
          alignmentDiffs.find((row) => row.diff_id === id) ?? null
        );
        return;
      }
      setSelectedDiff(null);
      loadRoundDetail(id);
    },
    [alignmentDiffs, loadRoundDetail]
  );

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-6">
      <div>
        <Title level={4} className="!mb-1">
          {t("evolutionBoard.title", { defaultValue: "知识演进看板" })}
        </Title>
        <Text className="text-sm text-neutral-500">
          {t("evolutionBoard.subtitle", {
            defaultValue:
              "时间轴（文档 diff + 演进轮次）· 三色 diff · 节点级版本对比。与 L7 演进闭环共用 EvolutionService 台账口径。",
          })}
        </Text>
      </div>

      {alignmentError ? (
        <Alert
          type="error"
          showIcon
          message={t("evolutionBoard.alignmentError", {
            defaultValue:
              "文档版本 diff 列表加载失败（GET /api/knowevo/alignment/diff/list）",
          })}
          action={
            <Button size="small" onClick={() => void loadAlignment()}>
              {t("common.retry", { defaultValue: "重试" })}
            </Button>
          }
        />
      ) : null}

      <div className="grid gap-4 lg:grid-cols-2">
        <EvolutionTimeline
          alignmentDiffs={alignmentDiffs}
          rounds={rounds}
          roundsFailure={roundsFailure}
          selectedId={
            selectedDiff?.diff_id ?? selectedRound?.round_id ?? detailRoundId
          }
          onSelect={onSelect}
          onRetryRounds={() => void loadRounds()}
        />
        {selectedDiff ? (
          <section className="rounded-lg border border-neutral-200 p-4">
            <AlignmentDiffDetail row={selectedDiff} />
          </section>
        ) : (
          <RoundDetailPanel
            report={selectedRound}
            loading={detailLoading}
            failure={detailFailure}
            onRetry={
              detailRoundId ? () => loadRoundDetail(detailRoundId) : undefined
            }
          />
        )}
      </div>

      <NodeVersionDiff />
    </div>
  );
}
