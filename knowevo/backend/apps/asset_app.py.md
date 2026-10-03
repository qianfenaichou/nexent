# asset_app.py —— 见 knowledge_graph_app.py.md §asset_app（合并规格）
本文件规格已并入 [knowledge_graph_app.py.md](knowledge_graph_app.py.md) 的 asset_app 章节。
独立保留此占位仅为对齐目录清单；实现时曾二选一：
A) 独立文件（预留给 data-process 5012 联动的回调端点）
B) 并入 knowledge_graph_app（推荐：若原生知识库 API 已覆盖登记需求）

> **2026-09-29 A/B 悬置落定：B（并入 knowledge_graph_app）**——零新 app 文件、
> 检索端点挂 `/api/knowevo` 命名空间。A 文保留为历史决策记录，不删。
> **服务层已落地（2026-09-29）：`DocAssetService.search_assets`**
> （`backend/services/knowevo/doc_asset_service.py`，契约见
> `backend/services/knowevo/doc_asset_service.py.md`）；**HTTP 端点未接线（后续接入）**。
