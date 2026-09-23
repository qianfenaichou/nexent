"use client";

// Ontology proposal queue with the A/X/P keyboard flow (K1 ss3): each
// card shows proposal + evidence anchor + impact summary + confidence
// only (information-minimized confirm loop, <=3 keystrokes per decision).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Empty,
  Input,
  Modal,
  Popover,
  Spin,
  Tag,
  message,
} from "antd";
import {
  CheckOutlined,
  CloseOutlined,
  PartitionOutlined,
} from "@ant-design/icons";
import type {
  OntologyProposal,
  OntologyQueuePage,
  OntologyVersionRow,
} from "@/types/knowledgeGraph";
import { ontologyService } from "@/services/knowledgeGraphService";

interface ConfirmOutcome {
  id: string;
  action: "confirmed" | "rejected" | "reparented";
}

interface Props {
  pageSize?: number;
  onSessionCommitted?: (row: OntologyVersionRow) => void;
}

const PAGE_DEFAULT = 40; // K1 ss3: batch ceiling per expert session

export function ProposalQueue({
  pageSize = PAGE_DEFAULT,
  onSessionCommitted,
}: Props) {
  const { t } = useTranslation();
  const [data, setData] = useState<OntologyQueuePage | null>(null);
  const [loading, setLoading] = useState(true);
  // Load failure is rendered as its own state: it must never fall through
  // to the "queue is empty" Empty state.
  const [loadError, setLoadError] = useState(false);
  const [cursor, setCursor] = useState(0); // index within current page
  const [outcomes, setOutcomes] = useState<ConfirmOutcome[]>([]);
  const [reparentFor, setReparentFor] = useState<OntologyProposal | null>(null);
  const [committing, setCommitting] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const page = await ontologyService.listProposals({
        page: 1,
        pageSize,
        status: "pending",
      });
      setData(page);
      setCursor(0);
      setOutcomes([]);
      setLoadError(false);
    } catch {
      // Drop the stale page so the A/X/P keyboard flow cannot act on it,
      // and surface a persistent error state (not a transient toast).
      setData(null);
      setLoadError(true);
    } finally {
      setLoading(false);
    }
  }, [pageSize]);

  useEffect(() => {
    void load();
  }, [load]);

  const items = data?.items ?? [];
  const current = items[cursor];

  const applyAction = useCallback(
    async (
      proposal: OntologyProposal,
      action: "confirm" | "reject",
      reason?: string
    ) => {
      try {
        await ontologyService.reviewProposals({
          action,
          ids: [proposal.id],
          rejectReason: reason,
        });
        setOutcomes((prev) => [
          ...prev,
          {
            id: proposal.id,
            action: action === "confirm" ? "confirmed" : "rejected",
          },
        ]);
        setCursor((c) => c + 1);
      } catch {
        message.error(
          t("knowledgeGraph.queue.reviewFailed", {
            defaultValue: "操作失败，请重试",
          })
        );
      }
    },
    [t]
  );

  const applyReparent = useCallback(
    async (proposal: OntologyProposal, newParent: string) => {
      try {
        await ontologyService.reviewProposals({
          action: "reparent",
          ids: [proposal.id],
          newParent,
        });
        setOutcomes((prev) => [
          ...prev,
          { id: proposal.id, action: "reparented" },
        ]);
        setCursor((c) => c + 1);
      } catch {
        message.error(
          t("knowledgeGraph.queue.reparentFailed", {
            defaultValue: "改父类失败（可能是成环）",
          })
        );
      }
    },
    [t]
  );

  // Keyboard flow: A confirm / X reject / P reparent. Guarded against
  // firing while the reparent modal or an input has focus.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (reparentFor || committing) return;
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (!current) return;
      if (e.key === "a" || e.key === "A") {
        e.preventDefault();
        void applyAction(current, "confirm");
      } else if (e.key === "x" || e.key === "X") {
        e.preventDefault();
        void applyAction(current, "reject");
      } else if (e.key === "p" || e.key === "P") {
        e.preventDefault();
        setReparentFor(current);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current, applyAction, reparentFor, committing]);

  const commitSession = useCallback(async () => {
    if (outcomes.length === 0) return;
    setCommitting(true);
    try {
      const confirmed = outcomes
        .filter((o) => o.action !== "rejected")
        .map((o) => o.id);
      const row = await ontologyService.commitVersion({
        confirmedIds: confirmed,
      });
      message.success(
        t("knowledgeGraph.queue.committed", {
          defaultValue: "已提交版本 {{version}}",
          version: row.version,
        })
      );
      onSessionCommitted?.(row);
      await load();
    } catch {
      message.error(
        t("knowledgeGraph.queue.commitFailed", { defaultValue: "提交版本失败" })
      );
    } finally {
      setCommitting(false);
    }
  }, [outcomes, load, onSessionCommitted, t]);

  const done = cursor >= items.length;
  const reviewedStats = useMemo(() => {
    const confirmed = outcomes.filter((o) => o.action !== "rejected").length;
    return { confirmed, rejected: outcomes.length - confirmed };
  }, [outcomes]);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spin />
      </div>
    );
  }

  if (loadError) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("knowledgeGraph.queue.loadFailed", {
          defaultValue: "加载提案队列失败",
        })}
        action={
          <Button size="small" onClick={() => void load()}>
            {t("common.retry", { defaultValue: "重试" })}
          </Button>
        }
      />
    );
  }

  if (items.length === 0) {
    return (
      <Empty
        description={t("knowledgeGraph.queue.empty", {
          defaultValue: "队列为空，等待新提案",
        })}
      />
    );
  }

  return (
    <div ref={listRef} className="flex flex-col gap-3">
      {current ? (
        <div
          className="rounded-lg border border-border p-4"
          data-testid="proposal-card"
        >
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Tag color="blue">{current.op}</Tag>
            <span className="text-lg font-semibold">
              {current.payload.name ?? current.target}
            </span>
            {current.payload.parent && (
              <span className="text-sm text-neutral-500">
                {t("knowledgeGraph.queue.parent", { defaultValue: "父类" })}:{" "}
                {current.payload.parent}
              </span>
            )}
          </div>
          <div className="mb-3 flex flex-wrap gap-2">
            <Tag>
              {t("knowledgeGraph.queue.confidence", { defaultValue: "置信度" })}
              : {current.confidence ?? "-"}
            </Tag>
            <Tag>
              {t("knowledgeGraph.queue.score", { defaultValue: "排序分" })}:{" "}
              {current.score}
            </Tag>
            <Tag color={current.ev_rich === 1 ? "green" : "orange"}>
              ev_rich {current.ev_rich}
            </Tag>
          </div>
          {/* Evidence anchors: hover preview that never interrupts the queue */}
          <div className="mb-3 flex flex-col gap-1">
            {(current.payload.evidence_spans ?? [])
              .slice(0, 3)
              .map((span, i) => (
                <Popover
                  key={i}
                  content={
                    <div className="max-w-md text-sm">
                      <div className="mb-1 font-medium">{span.doc}</div>
                      <div className="whitespace-pre-wrap">{span.text}</div>
                    </div>
                  }
                  title={t("knowledgeGraph.queue.evidence", {
                    defaultValue: "证据锚点",
                  })}
                >
                  <div className="cursor-help truncate rounded bg-neutral-50 px-2 py-1 text-sm dark:bg-neutral-800">
                    [{span.doc}] {span.text}
                  </div>
                </Popover>
              ))}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              type="primary"
              icon={<CheckOutlined />}
              onClick={() => void applyAction(current, "confirm")}
            >
              {t("knowledgeGraph.queue.confirm", { defaultValue: "确认 (A)" })}
            </Button>
            <Button
              danger
              icon={<CloseOutlined />}
              onClick={() => void applyAction(current, "reject")}
            >
              {t("knowledgeGraph.queue.reject", { defaultValue: "否决 (X)" })}
            </Button>
            <Button
              icon={<PartitionOutlined />}
              onClick={() => setReparentFor(current)}
            >
              {t("knowledgeGraph.queue.reparent", {
                defaultValue: "改父类 (P)",
              })}
            </Button>
          </div>
        </div>
      ) : (
        <Alert
          type="success"
          message={t("knowledgeGraph.queue.batchDone", {
            defaultValue: "本批已审完",
          })}
          description={`${t("knowledgeGraph.queue.confirmedCount", { defaultValue: "已确认" })} ${reviewedStats.confirmed} / ${t("knowledgeGraph.queue.rejectedCount", { defaultValue: "已否决" })} ${reviewedStats.rejected}`}
        />
      )}

      <div className="flex items-center justify-between">
        <span className="text-sm text-neutral-500">
          {cursor}/{items.length}
          {data && data.total > items.length
            ? ` (${t("knowledgeGraph.queue.total", { defaultValue: "共" })} ${data.total})`
            : ""}
        </span>
        <Button
          type="primary"
          disabled={outcomes.length === 0 || done}
          loading={committing}
          onClick={() => void commitSession()}
        >
          {t("knowledgeGraph.queue.commit", { defaultValue: "提交版本" })}
        </Button>
      </div>

      <Modal
        open={!!reparentFor}
        title={t("knowledgeGraph.queue.reparentTitle", {
          defaultValue: "指定新父类",
        })}
        okText={t("knowledgeGraph.queue.reparentOk", {
          defaultValue: "改父类",
        })}
        cancelText={t("common.cancel", { defaultValue: "取消" })}
        destroyOnClose
        onCancel={() => setReparentFor(null)}
        onOk={() => {
          const input =
            document.querySelector<HTMLInputElement>("#kw-reparent-input");
          const val = input?.value.trim();
          if (reparentFor && val) {
            void applyReparent(reparentFor, val);
            setReparentFor(null);
          }
        }}
      >
        <p className="mb-2 text-sm">{reparentFor?.payload.name} → ?</p>
        <Input id="kw-reparent-input" placeholder="Antidiabetic" autoFocus />
      </Modal>
    </div>
  );
}
