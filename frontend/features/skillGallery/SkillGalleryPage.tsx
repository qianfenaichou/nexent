"use client";

// Skill gallery page: card gallery + one-click instantiate.
// Route (pending wiring): /skillGallery. Does NOT replace skillTemplate
// panel - that file is left untouched; this is the gallery surface.
import { useTranslation } from "react-i18next";
import { Typography } from "antd";
import { SkillGallery } from "./components/SkillGallery";

const { Text, Title } = Typography;

export default function SkillGalleryPage() {
  const { t } = useTranslation();
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-4 px-4 py-6">
      <div>
        <Title level={4} className="!mb-1">
          {t("skillGallery.title", { defaultValue: "Skill 画廊" })}
        </Title>
        <Text className="text-sm text-neutral-500">
          {t("skillGallery.subtitle", {
            defaultValue:
              "参数化 SKILL.md 模板卡片墙：一键实例化（变量注入 → SKILL.md）· 复用统计诚实展示 · 不替换既有 skillTemplate 表格页",
          })}
        </Text>
      </div>
      <SkillGallery />
    </div>
  );
}
