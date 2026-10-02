"use client";

// Document-version event detail (wired /alignment/diff/list row). Shown
// when the timeline selects a diff event so selection is never a dead end.
import { useTranslation } from "react-i18next";
import { Descriptions, Empty, Tag, Typography } from "antd";
import type { AlignmentDiffRow } from "../types";

const { Text } = Typography;

export function AlignmentDiffDetail({ row }: { row: AlignmentDiffRow | null }) {
  const { t } = useTranslation();

  if (!row) {
    return (
      <Empty
        description={t("evolutionBoard.alignDetail.empty", {
          defaultValue: "在左侧选择一条文档版本 diff 事件",
        })}
      />
    );
  }

  const counts = Object.entries(row.change_counts ?? {}).filter(
    ([, n]) => n > 0
  );

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Text strong>
          {row.old_asset_no ?? "?"} → {row.new_asset_no ?? "?"}
        </Text>
        <Tag color="blue">
          {t("evolutionBoard.timeline.docDiff", {
            defaultValue: "文档版本 diff",
          })}
        </Tag>
      </div>
      <Descriptions
        size="small"
        column={1}
        className="mb-2"
        items={[
          {
            key: "at",
            label: t("evolutionBoard.detail.at", { defaultValue: "时间" }),
            children: row.created_at ?? "-",
          },
          {
            key: "diffId",
            label: "diff_id",
            children: (
              <Text code className="text-xs">
                {row.diff_id}
              </Text>
            ),
          },
        ]}
      />
      <Text strong className="mb-1 block">
        {t("evolutionBoard.alignDetail.changeCounts", {
          defaultValue: "变更计数",
        })}
      </Text>
      {counts.length === 0 ? (
        <Text type="secondary" className="text-xs">
          {t("evolutionBoard.alignDetail.noCounts", {
            defaultValue:
              "该 diff 无 change_type 计数（空 changes 或未归类）。",
          })}
        </Text>
      ) : (
        <div className="flex flex-wrap gap-1">
          {counts.map(([kind, n]) => (
            <Tag key={kind}>
              {kind}: {n}
            </Tag>
          ))}
        </div>
      )}
    </div>
  );
}
