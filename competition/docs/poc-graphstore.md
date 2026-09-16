# T-07a PoC 基准报告（A1 图存储）

> 日期：2026-09-17 · 数据：`gen_synthetic_graph` 合成图（seed=42，确定性）· 库：本地 PG 15（supabase-db-mini 5436，knowevo_test）
> 目标（备忘录 09 §3）：P1 多跳 p95<1.5s @2万/3万 · P2 批量 supersede p95<200ms

## 结果

| 探针 | 目标 | 实测 | 判定 |
|---|---|---|---|
| P1 多跳（beam=3, depth=3, 20 seeds） | p95 < 1500ms | **p95=12.5ms**（mean 8.2ms） | ✅ 快 ~120× |
| P2 批量 supersede（100 边/批） | p95 < 200ms | **p95=22.7ms** | ✅ 快 ~9× |
| upsert 2 万实体 | — | 79.4s（逐行 session） | ⚠️ 见下 |
| upsert 3 万边 | — | 120.2s（逐行 session） | ⚠️ 见下 |

## 环境与方法

- 宿主：本机（Linux），PG 15.8 容器 supabase-db-mini，`nexent` schema 13 表由迁移 001+002 建。
- 图：20,000 实体（15 类均衡）/ 30,000 边（8 种 rel_type 均衡），随机优先附着保持连通。
- 多跳：`PgJsonbGraphStore.multi_hop(seeds, HopPlan(), beam=3, depth=3)`，20 个随机种子，每个测一次，p95 取排序后 95 分位。
- supersede：`supersede(edge_ids, now, reason)`，每批 100 条。

## 结论

1. **查询路径无瓶颈**：多跳与 supersede 都在毫秒级，预留 100× 余量给 T-09 的 PPR/embedding 打分。PG JSONB + 索引方案（备忘录 09 §5 决策）维持。
2. **写入路径是短板**：upsert 逐行开 session（~4ms/行），2 万实体需 80s。量级翻倍（T-02 58 份语料真实抽取规模远小于此）可接受；若 T-10 评测批量灌图需提速，改 `bulk_save_objects` + 单 session（预计 10×）。这是 T-07b/T-08 的优化项，登记待办。
3. **集成测试门控已真跑**：`RUN_POSTGRES_INTEGRATION=1` 全 knowevo 131 测试在真实 PG 通过——T-06 之前 3 个集成测试从未在真库跑，本次抓出并修复 1 个真实 bug（LLM `new` 裁决被误降级 pending_review，见 pitfalls #25）。

## 复现

```bash
docker start supabase-db-mini
docker exec supabase-db-mini psql -U supabase_admin -d supabase -c "CREATE DATABASE knowevo_test" 2>/dev/null || true
docker exec -i supabase-db-mini psql -U supabase_admin -d knowevo_test -v ON_ERROR_STOP=1 < deploy/sql/migrations/v2.5.5_kw_001_knowevo_core.sql
cd backend
POSTGRES_HOST=localhost POSTGRES_PORT=5436 POSTGRES_USER=supabase_admin NEXENT_POSTGRES_PASSWORD=Huawei123 POSTGRES_DB=knowevo_test RUN_POSTGRES_INTEGRATION=1 uv run pytest ../test/backend/services/knowevo/ -q
POSTGRES_HOST=localhost POSTGRES_PORT=5436 POSTGRES_USER=supabase_admin NEXENT_POSTGRES_PASSWORD=Huawei123 POSTGRES_DB=knowevo_test uv run python -m services.knowevo.pipeline.gen_synthetic_graph --entities 20000 --edges 30000
```
