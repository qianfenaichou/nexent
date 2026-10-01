# asset_app.py —— 见 knowledge_graph_app.py.md §asset_app（合并规格）
本文件规格已并入 [knowledge_graph_app.py.md](knowledge_graph_app.py.md) 的 asset_app 章节。
独立保留此占位仅为对齐 03 计划 §1.1 的目录清单；实现时可二选一：
A) 独立文件（预留给 data-process 5012 联动的回调端点）
B) 并入 knowledge_graph_app（推荐：若 T-02 确认原生知识库 API 覆盖登记需求）

> **2026-09-29 A/B 悬置落定：B（并入 knowledge_graph_app）**。拍板链见
> `competition/docs/tech-optimization-2026-09-28/asset-search-立项-2026-09-28.md` §7-2
> （AI 裁定：本占位文件原文推荐 B、零新 app 文件、检索端点挂 `/api/knowevo` 命名空间；用户确认）。
> A 文保留为历史决策记录，不删。**服务层已落地（2026-09-29 M1）：`DocAssetService.search_assets`**
> （`backend/services/knowevo/doc_asset_service.py`，契约见
> `backend/services/knowevo/doc_asset_service.py.md`）；**HTTP 端点未接线（归 T-08）**。
