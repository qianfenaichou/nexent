"use client";

// One template card in the gallery: task-type badge, domain/version, reuse
// stats (never invent 0% success), and one-click instantiate entry.
// Keyboard: the card body is a button so Enter/Space opens instantiate.
import { useTranslation } from "react-i18next";
import { Button, Card, Space, Tag, Tooltip, Typography } from "antd";
import { ThunderboltOutlined } from "@ant-design/icons";
import type { SkillTemplate } from "@/types/skillTemplate";

const { Text } = Typography;

export const TASK_TYPE_COLORS: Record<string, string> = {
  fact_lookup: "geekblue",
  reasoning_decision: "purple",
  version_compare: "gold",
  refusal: "red",
  general_qa: "default",
};

export function TemplateCard({
  template,
  onInstantiate,
  onPreview,
}: {
  template: SkillTemplate;
  onInstantiate: (tpl: SkillTemplate) => void;
  onPreview: (tpl: SkillTemplate) => void;
}) {
  const { t } = useTranslation();
  const reuseSuccess = template.reuse_success;

  return (
    <Card
      size="small"
      className="h-full"
      title={
        <Space size={4} wrap>
          <Text strong className="break-all">
            {template.name}
          </Text>
          <Tag color={TASK_TYPE_COLORS[template.task_type] ?? "default"}>
            {template.task_type}
          </Tag>
        </Space>
      }
      actions={[
        <Button
          key="instantiate"
          type="primary"
          size="small"
          icon={<ThunderboltOutlined />}
          onClick={() => onInstantiate(template)}
          aria-label={`${t("skillGallery.instantiate", { defaultValue: "一键实例化" })} ${template.name}`}
        >
          {t("skillGallery.instantiate", { defaultValue: "一键实例化" })}
        </Button>,
        <Button
          key="preview"
          size="small"
          onClick={() => onPreview(template)}
          aria-label={`${t("skillGallery.preview", { defaultValue: "预览原文" })} ${template.name}`}
        >
          {t("skillGallery.preview", { defaultValue: "预览原文" })}
        </Button>,
      ]}
    >
      <button
        type="button"
        className="w-full cursor-pointer border-0 bg-transparent p-0 text-left"
        aria-label={`${t("skillGallery.instantiate", { defaultValue: "一键实例化" })} ${template.name}`}
        onClick={() => onInstantiate(template)}
      >
        <Space direction="vertical" size={4} className="w-full">
          <Space size={4} wrap>
            <Tag>{template.domain ?? "-"}</Tag>
            <Tag>v{template.version}</Tag>
            {template.source?.induced_by ? (
              <Tag color="blue">{template.source.induced_by}</Tag>
            ) : null}
          </Space>
          <Text type="secondary" className="text-xs">
            {t("skillGallery.reuseCount", { defaultValue: "复用" })}{" "}
            {template.reuse_count ?? 0} ·{" "}
            {t("skillGallery.successRate", { defaultValue: "成功率" })}{" "}
            {reuseSuccess === null || reuseSuccess === undefined ? (
              <Tooltip
                title={t("skillGallery.successNotRecorded", {
                  defaultValue:
                    "尚未回写：只在真实运行后由 record_reuse_outcome 记录",
                })}
              >
                <span>
                  {t("skillGallery.successUnknown", { defaultValue: "未记录" })}
                </span>
              </Tooltip>
            ) : (
              `${(reuseSuccess * 100).toFixed(0)}%`
            )}
          </Text>
          {template.source?.support ? (
            <Text type="secondary" className="text-xs">
              {t("skillGallery.support", { defaultValue: "支持度" })}{" "}
              {template.source.support}
            </Text>
          ) : null}
        </Space>
      </button>
    </Card>
  );
}
