"use client";

// Skill-template library panel (T-20). Route: /skillTemplate. Browses the
// mined parameterized SKILL.md templates in skill_template_t: list (name /
// task type / version / reuse count / success rate), preview the raw
// pre-render body_md, copy to clipboard.
//
// DATA-SOURCE STATUS: the list is served by
// GET /api/knowevo/skill-template/list (read-only, workbench RBAC) -
// wired after the T-20 integration round deferred it (its brief
// authorized no HTTP route). The pending-wiring Alert below is the
// fetch-failure state, never fabricated rows. Instantiation itself is
// reachable today through the skill_template_apply MCP tool.
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Empty,
  Modal,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from "antd";
import type { ColumnsType } from "antd/es/table";
import { CopyOutlined } from "@ant-design/icons";
import type { SkillTemplate } from "@/types/skillTemplate";
import { skillTemplateService } from "@/services/skillTemplateService";

const { Text, Title } = Typography;

const TASK_TYPE_COLORS: Record<string, string> = {
  fact_lookup: "geekblue",
  reasoning_decision: "purple",
  version_compare: "gold",
  refusal: "red",
  general_qa: "default",
};

export default function SkillTemplatePanel() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  // Three distinct tail states (error / empty / pending-wiring) must never
  // collapse into one, per the DiffAndQualityPanel error/empty dichotomy
  // (pitfall #71): a transient fetch failure is its own recoverable state
  // with a retry affordance, NOT the permanent "route not wired" notice.
  const [templates, setTemplates] = useState<SkillTemplate[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [preview, setPreview] = useState<SkillTemplate | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    skillTemplateService
      .listTemplates()
      .then((rows) => {
        if (!cancelled) setTemplates(rows);
      })
      .catch(() => {
        // Transient fetch failure (outage / 403 / route gone): surface a
        // recoverable error state with a retry button, never fake data.
        if (!cancelled) {
          setTemplates(null);
          setLoadError(true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const copyBody = async (tpl: SkillTemplate) => {
    try {
      await navigator.clipboard.writeText(tpl.body_md);
      setCopied(tpl.name);
      message.success(
        t("skillTemplate.copied", { defaultValue: "已复制模板原文" })
      );
    } catch {
      message.error(
        t("skillTemplate.copyFailed", { defaultValue: "复制失败" })
      );
    }
  };

  const columns: ColumnsType<SkillTemplate> = [
    {
      title: t("skillTemplate.col.name", { defaultValue: "模板名" }),
      dataIndex: "name",
      key: "name",
      render: (name: string) => <Text strong>{name}</Text>,
    },
    {
      title: t("skillTemplate.col.taskType", { defaultValue: "任务类型" }),
      dataIndex: "task_type",
      key: "task_type",
      render: (taskType: string) => (
        <Tag color={TASK_TYPE_COLORS[taskType] ?? "default"}>{taskType}</Tag>
      ),
    },
    {
      title: t("skillTemplate.col.domain", { defaultValue: "领域" }),
      dataIndex: "domain",
      key: "domain",
      render: (domain: string | null) => domain ?? "-",
    },
    {
      title: t("skillTemplate.col.version", { defaultValue: "版本" }),
      dataIndex: "version",
      key: "version",
    },
    {
      title: t("skillTemplate.col.reuseCount", { defaultValue: "复用次数" }),
      dataIndex: "reuse_count",
      key: "reuse_count",
      render: (count: number | null) => count ?? 0,
    },
    {
      title: t("skillTemplate.col.reuseSuccess", { defaultValue: "成功率" }),
      dataIndex: "reuse_success",
      key: "reuse_success",
      render: (rate: number | null) =>
        rate === null || rate === undefined ? (
          <Tooltip
            title={t("skillTemplate.successNotRecorded", {
              defaultValue:
                "尚未回写：成功率只在真实运行后由 record_reuse_outcome 记录，apply 不虚构",
            })}
          >
            <Text type="secondary">
              {t("skillTemplate.successUnknown", { defaultValue: "未记录" })}
            </Text>
          </Tooltip>
        ) : (
          `${(rate * 100).toFixed(0)}%`
        ),
    },
    {
      title: t("skillTemplate.col.actions", { defaultValue: "操作" }),
      key: "actions",
      render: (_, tpl) => (
        <Space size={4}>
          <Button size="small" onClick={() => setPreview(tpl)}>
            {t("skillTemplate.preview", { defaultValue: "预览" })}
          </Button>
          <Tooltip title={t("skillTemplate.copy", { defaultValue: "复制" })}>
            <Button
              size="small"
              icon={<CopyOutlined />}
              onClick={() => copyBody(tpl)}
              aria-label={`${t("skillTemplate.copy", { defaultValue: "复制" })} ${tpl.name}`}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div className="mx-auto flex w-full max-w-5xl flex-col gap-4 px-4 py-6">
      <div>
        <Title level={4} className="!mb-1">
          {t("skillTemplate.title", { defaultValue: "技能模板库" })}
        </Title>
        <Text className="text-sm text-neutral-500">
          {t("skillTemplate.subtitle", {
            defaultValue:
              "从真实决策卡轨迹归纳的参数化 SKILL.md 模板（skill_template_t）：预览渲染前原文、复制实例化、复用统计诚实回写",
          })}
        </Text>
      </div>

      {/* Transient fetch failure: recoverable, with an explicit retry.
          Kept strictly separate from the permanent "pending wiring" notice
          below so the user can tell "route not built yet" apart from
          "request failed just now". */}
      {loadError && !loading ? (
        <Alert
          type="error"
          showIcon
          message={t("skillTemplate.loadFailed.title", {
            defaultValue: "模板列表加载失败",
          })}
          description={t("skillTemplate.loadFailed.body", {
            defaultValue:
              "模板列表请求失败（可能为网络波动或服务暂不可用），可重试；若持续失败，模板清单仍可用 psql 查询 nexent.skill_template_t 核实。",
          })}
          action={
            <Button size="small" onClick={() => void load()}>
              {t("common.retry", { defaultValue: "重试" })}
            </Button>
          }
        />
      ) : templates === null && !loading ? (
        <Alert
          type="warning"
          showIcon
          message={t("skillTemplate.pendingWiring.title", {
            defaultValue: "数据源待接线",
          })}
          description={t("skillTemplate.pendingWiring.body", {
            defaultValue:
              "模板列表的只读 HTTP 路由（GET /api/knowevo/skill-template/list）尚未接线：T-20 轮次的后端暴露仅有 skill_template_apply MCP 工具（模板实例化已可用），为不越权改动 T-19 领地（knowledge_graph_app.py），本页暂以真实状态示人而非编造数据。模板清单当前可用 psql 查询 nexent.skill_template_t 核实。",
          })}
        />
      ) : null}

      {loading ? (
        <div className="flex justify-center py-10">
          <Spin />
        </div>
      ) : !loadError && templates && templates.length > 0 ? (
        <Table<SkillTemplate>
          rowKey={(tpl) => tpl.name}
          columns={columns}
          dataSource={templates}
          pagination={false}
          size="small"
        />
      ) : !loading && !loadError && templates ? (
        <Empty
          description={t("skillTemplate.empty", {
            defaultValue: "尚无模板：先运行 mine_skill_templates 从决策卡归纳",
          })}
        />
      ) : null}

      <Modal
        open={preview !== null}
        title={preview?.name}
        onCancel={() => setPreview(null)}
        footer={
          preview ? (
            <Button
              type="primary"
              icon={<CopyOutlined />}
              onClick={() => copyBody(preview)}
            >
              {copied === preview.name
                ? t("skillTemplate.copied", { defaultValue: "已复制模板原文" })
                : t("skillTemplate.copy", { defaultValue: "复制" })}
            </Button>
          ) : null
        }
        width={720}
      >
        {preview ? (
          <>
            <Space size={4} wrap className="mb-2">
              <Tag color={TASK_TYPE_COLORS[preview.task_type] ?? "default"}>
                {preview.task_type}
              </Tag>
              <Tag>v{preview.version}</Tag>
              {preview.source?.induced_by ? (
                <Tag color="blue">{preview.source.induced_by}</Tag>
              ) : null}
              {preview.source?.support ? (
                <Tag>
                  {t("skillTemplate.support", { defaultValue: "支持度" })}{" "}
                  {preview.source.support}
                </Tag>
              ) : null}
            </Space>
            <Text type="secondary" className="mb-2 block text-xs">
              {t("skillTemplate.previewNote", {
                defaultValue:
                  "以下为渲染前的模板原文（body_md）；变量注入由 skill_template_apply MCP 工具完成。",
              })}
            </Text>
            <pre className="max-h-[60vh] overflow-auto rounded bg-neutral-50 p-3 text-xs leading-5 whitespace-pre-wrap">
              {preview.body_md}
            </pre>
          </>
        ) : null}
      </Modal>
    </div>
  );
}
