# T-25：决策卡版本对比 UI 接线（`as_of` 业务时间输入）

**状态**: ✅ 已完成（2026-09-21 r22 轮；来源 = `t23-capture2` findings：`DecisionCardPanel` 只发 `question/ontology_version/mode`，全仓前端 grep `as_of|asOf` = 0 命中 → T-23「新旧指南版本对比」截图在面板上不可达）
**Blocked by**: 无（后端已支持：r21 提交 `47bed07e0` 在 `POST /knowevo/decision/card` 开放可选 `as_of`；`ontology_version` 面板早已有）
**独占文件**（本任务创建/修改）:
- `frontend/features/decisionCard/DecisionCardPanel.tsx`
- `frontend/services/decisionCardService.ts`
- `frontend/types/decisionCard.ts`
- `frontend/public/locales/zh/common.json` / `frontend/public/locales/en/common.json`（**只增键**）
- `frontend/tests/decisionCardRequest.test.ts`（**新建**，node:test 纯函数测试）

**待接线项**: 无
**禁改清单**: `backend/**`（API 已就绪）、其他 `frontend/features/**`、`package.json`（**零新依赖**）、locales 既有键
**允许的新依赖**: **无**

**要构建的行为**（用户视角端到端）:
1. 决策卡面板新增可选「业务时间（as_of）」输入（`datetime-local` 或等价），旁注语义：**钉住事实时钟**（后端返回 `clock_source="explicit"` + `knowledge_stamp.kg_cutoff`）；
2. **防过度声称**：仅给 `as_of`（不带 `ontology_version`）时，后端刻意把 claim 级 `version_pinned` 降为 `false` → UI **不得**把它展示成"已版本钉住"；
3. `ontology_version` + `as_of` **同时**给定时提交（这是 T-23 版本对比配方），卡片区可见 `kg_cutoff` / `clock_source`（若已展示则不必重复）；
4. 请求构造抽成**纯函数**并测试：`buildDecisionCardRequest({question, ontologyVersion?, asOf?, mode})` → 仅在非空时携带 `as_of` / `ontology_version`；**不填 = 与改动前请求体逐键一致**（向后兼容）；
5. 一键清除 as_of（回到默认 now 语义）。

**验收命令**:
```bash
cd frontend
npx tsc --noEmit                                   # 或仓库既有 type-check 脚本
npx eslint features/decisionCard services/decisionCardService.ts
node --test tests/decisionCardRequest.test.ts
# 可选（pitfall #22：与全栈互斥，假死即降级并如实记录）：
npm run check-all
```

**验收标准**:
- [ ] 不填 `as_of` → 请求体与改动前**逐键一致**（测试锁定）
- [ ] 填 `as_of`（无版本）→ 请求含 `as_of`、不含 `ontology_version`；卡片**不得**出现"版本钉住/version-pinned"字样
- [ ] 版本 + `as_of` 同给 → 请求两者都在（T-23 配方）
- [ ] 新增 node:test ≥4 条（三态请求构造 + 清除行为）
- [ ] 零新依赖、零 backend 改动、locales 只增键、注释英文、行宽合规
- [ ] Evidence 填真实命令输出；`npm run check-all` 若未跑须如实写明原因

**Evidence**（2026-09-21 r22 / kw-asofui；分支 `fix/kw-r19-llm-extract-thinking`）:

```text
# node --test tests/decisionCardRequest.test.ts
✔ default request keeps the pre-T-25 body: question + mode only (0.74ms)
✔ mode defaults to full when omitted (0.09ms)
✔ as_of alone is carried without ontology_version (0.10ms)
✔ version + as_of are both carried (the T-23 version-compare recipe) (0.09ms)
✔ clearing as_of (empty string) drops the key and restores the old body (0.10ms)
ℹ tests 5 / pass 5 / fail 0 / duration_ms 73.98

# npx tsc --noEmit        -> exit 0 (no output)

# npx eslint ...
#   ESLint v9 默认 flat config，本仓只有 .eslintrc.json，故需 legacy 开关：
#   ESLINT_USE_FLAT_CONFIG=false npx eslint features/decisionCard services/decisionCardService.ts
#   -> 2 errors，均为 features/decisionCard/components/EvidenceChain.tsx 的
#      既有 prettier/prettier 告警（该文件未改动、不在本任务独占清单内，未触碰）；
#   ESLINT_USE_FLAT_CONFIG=false npx eslint \
#       features/decisionCard/DecisionCardPanel.tsx services/decisionCardService.ts
#   -> exit 0（本次改动文件零告警）。

# npm run check-all -> 未运行（pitfall #22：frontend `next build` 与在跑的全栈互斥；
#   E8 窗口 e8-r22b.pid 正在为截图服务）。验收改用 tsc + eslint + node --test。
```

补充（prettier 归一化）：对 `DecisionCardPanel.tsx` 执行 `prettier --write` 时，同一文件内**既有**超宽行（>80）被一并折行，故 diff 含少量与本任务无关的格式化 hunk；无逻辑变更，未触碰其它文件。

---

## 实现备注

1. **不要改后端**：`as_of` 已在 `DecisionCardRequest`（`backend/apps/knowledge_graph_app.py`）与 `decision_service.multi_hop(as_of=…)` 落地；本任务只做前端可达性。
2. **命名/文案**：中英双语；避免出现"已钉住版本"这类与后端语义不符的措辞（后端在无 `ontology_version` 时 claim 级 pinned=false）。
3. `t23-capture2` 会随后用本 UI 采 T-23「版本对比」截图（`T-23-version-compare-2020.png` / `-2024.png`），请保证"版本 + as_of"路径一次点击可达。
4. 零看门狗：不要新增任何 supervisor/心跳/WAKE 文件或代码。
