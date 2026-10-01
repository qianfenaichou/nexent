# asset_fusion.py —— asset_search 两路融合工厂（点火门 ≥2 路）
**归属任务**: T-08 融合二期（2026-09-30 新建）· 依据: `t08-phase2-recon-2026-09-30.md` §2.2-B + `l6-m2-id-space-decision-2026-09-29.md` §2 + L6-M2 verify §6-③

## 职责

组织 asset_search 两路（**统一在 `AssetHit.id` 空间**），调冻结内核 `rrf_fusion.fuse(lists, k=60)`，在**点火门**后返回融合序 + 审计：

- **bm25** = `AssetRawListClient.asset_bm25_hits`：同步 SDK 调用经 `asyncio.to_thread` 下放工作线程；
- **dense** = `AssetRawListClient.asset_dense_hits`：本回合诚实空表，冻结理由常量随 `AssetFusionOutcome.dense_reason` 入审计。

**点火门（反表演规则，L6-M2 verify §6-③）**：仅当 **≥2 路非空** 才把 fuse 结果交给调用方（`fired=True`）；单路（哪怕多条命中）一律 `fired=False`、`ids=()`，调用方**原样走今日路径**。门计的是**非空路数**，不是命中总数。dense 未就绪时只有 BM25 → 门恒关 → 默认零行为变化。

## 接口冻结

```python
@dataclass(frozen=True)
class AssetFusionOutcome:
    ids: tuple[str, ...]           # 完整融合序（AssetHit.id 空间）；fired=False 时为空
    hits: tuple[FusedHit, ...]     # 内核审计
    bm25_count: int
    dense_count: int
    fired: bool                    # 点火门：True 仅当 ≥2 路非空
    dense_reason: str = ASSET_INDEX_HAS_NO_EMBEDDING_FIELD

async def fused_asset_hits(client, tenant_id, query, *,
                           top_k=5, k=DEFAULT_RRF_K) -> AssetFusionOutcome: ...
```

`client` = `AssetRawListClient`（或 duck-typed fake）；`FusedHit` 复用 `rrf_fusion` 形状。`AssetFusionOutcome` 与 `kg_fusion.FusionOutcome` **有意不合并**（asset 无图路字段；不为抽象而抽象）。

## 语义（冻结）

- **两路拉取顺序**：bm25（线程池）→ dense（同步，恒 `[]`）；任一路返回 `None` 视为空表；
- **点火门**：`sum(1 for r in (bm25, dense) if r) >= 2` 才 `fired=True` 并 fuse；否则记录 counts、`ids=()`、`hits=()`；
- **融合**：`fuse([bm25, dense], k=k)`——跨路同 id 求和；路内重复取首次名次；平局 `(-score, best_rank, id)`（内核冻结）；
- **id 空间守卫**：hit 若带 `id_space` 标记且非 `"asset"`/缺失 → `ValueError`（禁止 document 空间静默混入）；
- **失败语义**：路内异常**向调用方传播**（静默回退属 handler call site）；`client is None` → `ValueError`；
- `k` 校验委托 `fuse`。

## 诚实边界

- dense 未实现 → 融合实际**两路满员做不到**；门关着不是缺陷，是诚实。dense 就绪后只补 `asset_dense_hits` 真查询，**融合内核零改**。
- 权威先验若应用只允许路内（进 fuse 前）；RRF 层禁止再乘 authority。
- 本模块不保证检索质量；`first_seen`/`ranks` 是唯一审计面，不向输出卡添 score。

## 验收锚点

- `pytest test/backend/services/knowevo/test_asset_fusion.py -v`：15 用例离线全绿（点火门矩阵 0/1/2 路 / 单路不表演 / 两路手算 RRF 与 ranks / 跨路重复求和 / None 视空 / 异常传播 / client None ValueError / k 转发 / 线程化调用参数 / 双跑确定 / id 空间为 str / 跨空间 ValueError）；
- 全量回归：`pytest ../test/backend/services/knowevo/ -q` 基线不降 + `ruff check backend/services/knowevo mcp_servers` 0 新违例；
- 契约双副本 IDENTICAL（根 `knowevo/` + 仓内副本）。
