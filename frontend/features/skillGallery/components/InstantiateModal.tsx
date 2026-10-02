"use client";

// Instantiate modal: variable form (defaults from template.variables) +
// deterministic render preview + one-click apply. Server apply is preferred;
// client_preview is labeled so reuse_count honesty is never broken.
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Space,
  Tag,
  Typography,
  message,
} from "antd";
import { CopyOutlined, ThunderboltOutlined } from "@ant-design/icons";
import type { SkillTemplate } from "@/types/skillTemplate";
import { skillGalleryService, type ApplyResult } from "../service";
import { renderSkillTemplate } from "../renderTemplate";

const { Text } = Typography;

export function InstantiateModal({
  template,
  onClose,
}: {
  template: SkillTemplate | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [form] = Form.useForm();
  const [applying, setApplying] = useState(false);
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});

  const variableKeys = useMemo(() => {
    if (!template) return [] as string[];
    const keys = new Set<string>();
    for (const k of Object.keys(template.variables ?? {})) keys.add(k);
    // Canonical injection points every template must expose.
    for (const k of [
      "domain",
      "task_type",
      "relation_template",
      "domain_rules",
    ]) {
      keys.add(k);
    }
    return Array.from(keys);
  }, [template]);

  useEffect(() => {
    if (!template) return;
    setResult(null);
    const init: Record<string, string> = {};
    for (const key of variableKeys) {
      const v = template.variables?.[key];
      init[key] = v !== undefined && v !== null ? String(v) : "";
    }
    form.setFieldsValue(init);
    setDraft(init);
  }, [template, variableKeys, form]);

  if (!template) return null;

  const previewBody = renderSkillTemplate(
    template.body_md,
    draft,
    template.name
  );

  const runApply = async () => {
    const values = form.getFieldsValue() as Record<string, string>;
    setApplying(true);
    try {
      const out = await skillGalleryService.applyTemplate(
        template.name,
        template.body_md,
        template.variables ?? {},
        values
      );
      setResult(out);
      message.success(
        out.via === "server"
          ? t("skillGallery.appliedServer", {
              defaultValue: "已实例化（服务端已记复用）",
            })
          : t("skillGallery.appliedPreview", {
              defaultValue: "已本地渲染实例（未改 reuse_count）",
            })
      );
    } catch {
      message.error(
        t("skillGallery.applyFailed", { defaultValue: "实例化失败" })
      );
    } finally {
      setApplying(false);
    }
  };

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      message.success(t("skillGallery.copied", { defaultValue: "已复制" }));
    } catch {
      message.error(t("skillGallery.copyFailed", { defaultValue: "复制失败" }));
    }
  };

  return (
    <Modal
      open={template !== null}
      title={`${t("skillGallery.instantiate", { defaultValue: "一键实例化" })} · ${template.name}`}
      onCancel={onClose}
      width={760}
      footer={
        <Space>
          <Button onClick={onClose}>
            {t("common.close", { defaultValue: "关闭" })}
          </Button>
          <Button
            type="primary"
            icon={<ThunderboltOutlined />}
            loading={applying}
            onClick={() => void runApply()}
          >
            {t("skillGallery.instantiate", { defaultValue: "一键实例化" })}
          </Button>
          <Button
            icon={<CopyOutlined />}
            onClick={() => void copyText(result?.skill_md ?? previewBody)}
          >
            {t("skillGallery.copyResult", { defaultValue: "复制实例文本" })}
          </Button>
          <Button
            icon={<CopyOutlined />}
            onClick={() => void copyText(template.body_md)}
          >
            {t("skillGallery.copyRaw", { defaultValue: "复制模板原文" })}
          </Button>
        </Space>
      }
    >
      <Form
        form={form}
        layout="vertical"
        size="small"
        onValuesChange={(changed) =>
          setDraft((prev) => ({ ...prev, ...changed }))
        }
      >
        {variableKeys.map((key) => (
          <Form.Item key={key} name={key} label={key}>
            <Input placeholder={`${key}（空则保留 {${key}} 占位）`} />
          </Form.Item>
        ))}
      </Form>

      {result?.via === "client_preview" ? (
        <Alert
          className="mb-2"
          type={result.reason === "forbidden" ? "error" : "info"}
          showIcon
          title={
            result.reason === "route_pending"
              ? t("skillGallery.clientPreview.title", {
                  defaultValue: "本地预览实例（服务端 apply 未接线）",
                })
              : result.reason === "forbidden"
                ? t("skillGallery.clientPreview.forbiddenTitle", {
                    defaultValue: "无权调用服务端实例化",
                  })
                : result.reason === "template_missing"
                  ? t("skillGallery.clientPreview.missingTitle", {
                      defaultValue: "模板不存在或请求不合法",
                    })
                  : t("skillGallery.clientPreview.errorTitle", {
                      defaultValue: "服务端实例化未成功",
                    })
          }
          description={
            result.reason === "route_pending"
              ? t("skillGallery.clientPreview.body", {
                  defaultValue:
                    "POST /api/knowevo/skill-template/apply 尚未接线（工单 L10-W10），本次仅做确定性占位替换；reuse_count 未增加。服务端实例化可用 MCP skill_template_apply。",
                })
              : result.reason === "forbidden"
                ? t("skillGallery.clientPreview.forbiddenBody", {
                    defaultValue:
                      "服务返回 403。已降级为本地占位替换，reuse_count 未增加；请用有权限的会话或 MCP skill_template_apply。",
                  })
                : result.reason === "template_missing"
                  ? t("skillGallery.clientPreview.missingBody", {
                      defaultValue:
                        "服务端拒绝了该模板名/变量。已降级本地渲染，reuse_count 未增加。",
                    })
                  : t("skillGallery.clientPreview.errorBody", {
                      defaultValue:
                        "服务端未返回可用 skill_md（网络或 5xx）。已降级本地渲染，reuse_count 未增加；可重试或用 MCP skill_template_apply。",
                    })
          }
          action={
            <Button size="small" onClick={() => void runApply()}>
              {t("common.retry", { defaultValue: "重试" })}
            </Button>
          }
        />
      ) : null}

      <div className="mb-1 flex flex-wrap items-center gap-2">
        <Text type="secondary" className="text-xs">
          {t("skillGallery.renderedPreview", {
            defaultValue: "实例化结果（SKILL.md）",
          })}
        </Text>
        {result ? (
          <Tag color={result.via === "server" ? "green" : "gold"}>
            {result.via === "server"
              ? t("skillGallery.viaServer", { defaultValue: "服务端" })
              : t("skillGallery.viaPreview", { defaultValue: "本地预览" })}
          </Tag>
        ) : null}
      </div>
      <pre
        className="max-h-[40vh] overflow-auto rounded bg-neutral-50 p-3 text-xs leading-5 whitespace-pre-wrap"
        aria-live="polite"
      >
        {result?.skill_md ?? previewBody}
      </pre>
    </Modal>
  );
}
