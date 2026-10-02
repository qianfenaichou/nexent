"use client";

// Round detail: orchestration steps (ok / skipped / failed / pending),
// cost, and non-reserved ops_summary op counts. Mirrors
// EvolutionService.RoundReport; steps that never ran stay visible as
// pending rather than omitted (contract rule). Reserved _-prefixed keys
// (see opsSummary.ts) are never painted as op counts.
//
// Failure honesty:
//   pending_wiring -> "route not registered"
//   unauthorized   -> 401 / expired login (re-login, not "missing")
//   forbidden      -> 403 tenant/RBAC (cross-tenant read refused)
//   other          -> generic unavailable; never invents steps
import { useTranslation } from "react-i18next";
import { Alert, Button, Descriptions, Empty, Tag, Typography } from "antd";
import type { EvolutionRoundReport, EvolutionStepStatus } from "../types";
import type { LoadFailureKind } from "../errorClass";
import { opCounts, opsStatus } from "../opsSummary";

const { Text } = Typography;

const STEP_COLOR: Record<EvolutionStepStatus, string> = {
  ok: "green",
  skipped: "default",
  failed: "red",
  pending: "gold",
};

type FailureCopy = {
  alertType: "error" | "warning";
  titleKey: string;
  titleDefault: string;
  bodyKey: string;
  bodyDefault: string;
  /** Retrying only helps when the failure is not a standing permission. */
  allowRetry: boolean;
};

// One entry per LoadFailureKind. Keying the map by the union means a new
// kind fails the type check here instead of falling through to copy that
// describes a different failure.
const FAILURE_COPY: Record<LoadFailureKind, FailureCopy> = {
  pending_wiring: {
    alertType: "warning",
    titleKey: "evolutionBoard.detail.unavailable.title",
    titleDefault: "轮次详情待接线",
    bodyKey: "evolutionBoard.detail.unavailable.body",
    bodyDefault:
      "GET /api/knowevo/evolution/rounds/{id} 尚未注册。不展示虚构步骤。",
    allowRetry: true,
  },
  unauthorized: {
    alertType: "warning",
    titleKey: "evolutionBoard.detail.unauthorized.title",
    titleDefault: "登录已失效，无法读取该轮次",
    bodyKey: "evolutionBoard.detail.unauthorized.body",
    bodyDefault:
      "服务返回 401：当前登录凭据已过期或无效，请重新登录后再打开该轮次。不是轮次缺失。",
    allowRetry: true,
  },
  forbidden: {
    alertType: "error",
    titleKey: "evolutionBoard.detail.forbidden.title",
    titleDefault: "无权读取该轮次",
    bodyKey: "evolutionBoard.detail.forbidden.body",
    bodyDefault:
      "服务返回 403：该轮次属于其他租户或当前角色无权读取。不是空数据。",
    allowRetry: false,
  },
  not_found: {
    alertType: "error",
    titleKey: "evolutionBoard.detail.missing.title",
    titleDefault: "轮次不存在",
    bodyKey: "evolutionBoard.detail.missing.body",
    bodyDefault:
      "服务返回 404：未找到该 round_id 的演进记录。不展示虚构步骤。",
    allowRetry: true,
  },
  server: {
    alertType: "warning",
    titleKey: "evolutionBoard.detail.unavailable.title",
    titleDefault: "轮次详情不可用",
    bodyKey: "evolutionBoard.detail.errorBody",
    bodyDefault: "请求失败（网络或服务端错误）。不展示虚构步骤。",
    allowRetry: true,
  },
  network: {
    alertType: "warning",
    titleKey: "evolutionBoard.detail.unavailable.title",
    titleDefault: "轮次详情不可用",
    bodyKey: "evolutionBoard.detail.errorBody",
    bodyDefault: "请求失败（网络或服务端错误）。不展示虚构步骤。",
    allowRetry: true,
  },
};

function FailureAlert({
  kind,
  onRetry,
}: {
  kind: LoadFailureKind;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();
  const copy = FAILURE_COPY[kind];
  return (
    <Alert
      type={copy.alertType}
      showIcon
      title={t(copy.titleKey, { defaultValue: copy.titleDefault })}
      description={t(copy.bodyKey, { defaultValue: copy.bodyDefault })}
      action={
        copy.allowRetry && onRetry ? (
          <Button size="small" onClick={onRetry}>
            {t("common.retry", { defaultValue: "重试" })}
          </Button>
        ) : null
      }
    />
  );
}

export function RoundDetailPanel({
  report,
  loading,
  failure,
  onRetry,
}: {
  report: EvolutionRoundReport | null;
  loading: boolean;
  failure: LoadFailureKind | null;
  onRetry?: () => void;
}) {
  const { t } = useTranslation();

  if (loading) {
    return (
      <section
        className="rounded-lg border border-neutral-200 p-4"
        aria-busy="true"
      >
        <Text type="secondary">
          {t("evolutionBoard.detail.loading", {
            defaultValue: "加载轮次详情…",
          })}
        </Text>
      </section>
    );
  }

  if (failure) {
    return (
      <section className="rounded-lg border border-neutral-200 p-4">
        <FailureAlert kind={failure} onRetry={onRetry} />
      </section>
    );
  }

  if (!report) {
    return (
      <section className="rounded-lg border border-neutral-200 p-4">
        <Empty
          description={t("evolutionBoard.detail.empty", {
            defaultValue: "在左侧时间轴选择一轮演进或一次文档 diff",
          })}
        />
      </section>
    );
  }

  const counts = opCounts(report.ops_summary);
  const status = report.status || opsStatus(report.ops_summary);

  return (
    <section className="rounded-lg border border-neutral-200 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Text strong>{report.round_id}</Text>
        <Tag>{status}</Tag>
        {report.trigger_source ? (
          <Tag color="blue">{report.trigger_source}</Tag>
        ) : null}
        {report.rollback_of ? (
          <Tag color="orange">
            {t("evolutionBoard.detail.rollbackOf", {
              defaultValue: "回滚自",
            })}{" "}
            {report.rollback_of}
          </Tag>
        ) : null}
      </div>

      <Descriptions
        size="small"
        column={2}
        className="mb-3"
        items={[
          {
            key: "at",
            label: t("evolutionBoard.detail.at", { defaultValue: "时间" }),
            children: report.at ?? report.created_at ?? "-",
          },
          {
            key: "tokens",
            label: t("evolutionBoard.detail.tokens", {
              defaultValue: "tokens",
            }),
            children: report.cost?.tokens ?? "-",
          },
          {
            key: "humanMinutes",
            label: t("evolutionBoard.detail.humanMinutes", {
              defaultValue: "人审分钟",
            }),
            children: report.cost?.human_minutes ?? "-",
          },
          {
            key: "edgesSuperseded",
            label: t("evolutionBoard.detail.edgesSuperseded", {
              defaultValue: "失效边数",
            }),
            children: report.edges_superseded_ids?.length ?? 0,
          },
        ]}
      />

      <Text strong className="mb-2 block">
        {t("evolutionBoard.detail.ops", { defaultValue: "操作计数" })}
      </Text>
      {counts.length === 0 ? (
        <Text type="secondary" className="mb-3 block text-xs">
          {t("evolutionBoard.detail.opsEmpty", {
            defaultValue:
              "无非保留 ops 计数（或仅有 _status/_steps 等保留键）。",
          })}
        </Text>
      ) : (
        <div className="mb-3 flex flex-wrap gap-1">
          {counts.map(({ key, count }) => (
            <Tag key={key}>
              {key}: {count}
            </Tag>
          ))}
        </div>
      )}

      <Text strong className="mb-2 block">
        {t("evolutionBoard.detail.steps", { defaultValue: "编排步骤" })}
      </Text>
      <ul className="m-0 list-none space-y-2 p-0">
        {(report.steps ?? []).map((step) => (
          <li key={step.name} className="flex flex-wrap items-start gap-2">
            <Tag color={STEP_COLOR[step.status] ?? "default"}>
              {step.status}
            </Tag>
            <Text code className="text-xs">
              {step.name}
            </Text>
            {step.detail ? (
              <Text type="secondary" className="text-xs">
                {step.detail}
              </Text>
            ) : null}
          </li>
        ))}
        {(report.steps ?? []).length === 0 ? (
          <li>
            <Text type="secondary" className="text-xs">
              {t("evolutionBoard.detail.noSteps", {
                defaultValue:
                  "该轮次未缓存逐步轨迹（服务重启后 steps 仅存 ops_summary）。",
              })}
            </Text>
          </li>
        ) : null}
      </ul>
    </section>
  );
}
