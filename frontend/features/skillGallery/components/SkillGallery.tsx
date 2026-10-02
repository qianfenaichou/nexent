"use client";

// Card gallery over mined skill templates + one-click instantiate modal.
// Tail states stay split (loading / error / empty / grid) per.
// Keyboard: "/" focuses the filter field (WCAG 2.2 AA reachability).
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  Alert,
  Button,
  Col,
  Empty,
  Input,
  Modal,
  Row,
  Spin,
  Typography,
} from "antd";
import type { InputRef } from "antd";
import type { SkillTemplate } from "@/types/skillTemplate";
import { skillGalleryService } from "../service";
import { TemplateCard } from "./TemplateCard";
import { InstantiateModal } from "./InstantiateModal";

const { Text } = Typography;

export function SkillGallery() {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(false);
  const [templates, setTemplates] = useState<SkillTemplate[] | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [query, setQuery] = useState("");
  const [instantiate, setInstantiate] = useState<SkillTemplate | null>(null);
  const [preview, setPreview] = useState<SkillTemplate | null>(null);
  const searchRef = useRef<InputRef>(null);

  const load = useCallback(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(false);
    skillGalleryService
      .listTemplates()
      .then((rows) => {
        if (!cancelled) setTemplates(rows);
      })
      .catch(() => {
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
    const cancel = load();
    return cancel;
  }, [load]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || target?.isContentEditable) {
        return;
      }
      e.preventDefault();
      searchRef.current?.focus();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const visible = (templates ?? []).filter((tpl) => {
    if (!query.trim()) return true;
    const q = query.trim().toLowerCase();
    return (
      tpl.name.toLowerCase().includes(q) ||
      tpl.task_type.toLowerCase().includes(q) ||
      (tpl.domain ?? "").toLowerCase().includes(q)
    );
  });

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          ref={searchRef}
          allowClear
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={t("skillGallery.search", {
            defaultValue: "按名称 / 任务类型 / 领域过滤（按 / 聚焦）",
          })}
          style={{ maxWidth: 320 }}
          aria-label={t("skillGallery.search", {
            defaultValue: "按名称 / 任务类型 / 领域过滤（按 / 聚焦）",
          })}
        />
        <Button onClick={() => void load()}>
          {t("common.retry", { defaultValue: "刷新" })}
        </Button>
        {templates ? (
          <Text type="secondary" className="text-xs">
            {t("skillGallery.count", { defaultValue: "显示" })} {visible.length}
            {" / "}
            {templates.length}
          </Text>
        ) : null}
      </div>

      {loadError && !loading ? (
        <Alert
          type="error"
          showIcon
          title={t("skillGallery.loadFailed.title", {
            defaultValue: "模板列表加载失败",
          })}
          description={t("skillGallery.loadFailed.body", {
            defaultValue:
              "GET /api/knowevo/skill-template/list 请求失败；若持续失败可用 psql 查询 nexent.skill_template_t 核实。",
          })}
          action={
            <Button size="small" onClick={() => void load()}>
              {t("common.retry", { defaultValue: "重试" })}
            </Button>
          }
        />
      ) : null}

      {loading ? (
        <div className="flex justify-center py-10">
          <Spin />
        </div>
      ) : !loadError && templates && visible.length > 0 ? (
        <Row gutter={[12, 12]}>
          {visible.map((tpl) => (
            <Col key={tpl.name} xs={24} sm={12} lg={8} xl={6}>
              <TemplateCard
                template={tpl}
                onInstantiate={setInstantiate}
                onPreview={setPreview}
              />
            </Col>
          ))}
        </Row>
      ) : !loading && !loadError && templates ? (
        <Empty
          description={
            query.trim()
              ? t("skillGallery.emptyFilter", {
                  defaultValue: "无匹配模板",
                })
              : t("skillGallery.empty", {
                  defaultValue:
                    "尚无模板：先运行 mine_skill_templates 从决策卡归纳",
                })
          }
        />
      ) : null}

      <InstantiateModal
        template={instantiate}
        onClose={() => setInstantiate(null)}
      />

      <Modal
        open={preview !== null}
        title={preview?.name}
        onCancel={() => setPreview(null)}
        footer={null}
        width={720}
      >
        {preview ? (
          <>
            <Text type="secondary" className="mb-2 block text-xs">
              {t("skillGallery.previewNote", {
                defaultValue:
                  "渲染前模板原文（body_md）。变量注入走「一键实例化」或 MCP skill_template_apply。",
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
