# T-00：仓库引导
**Blocked by**: 无
**独占文件**: competition/（tasks/docs/deliverables）、knowevo/（框架包只读参考）、THIRD_PARTY_NOTICE.md、main 分支
**待接线项**: 无
**禁改清单**: backend/、frontend/、sdk/、deploy/ 全部上游代码未动
**允许的新依赖**: 无新增
**本任务细节**: 拷入 knowevo/ 框架包为本仓 competition 同级参考；ADR 文件随包入库（tasks/README.md T-00 行）
**要构建的行为**: 稳定演示分支 main（与 develop 同基线 69e85c8）+ 竞赛工作目录就位
**验收命令**: `git branch -v`；`find competition knowevo -type f | wc -l`；`git status --short`
**验收标准**:
- [x] main 分支存在且指向 69e85c8（develop 保持集成分支，工作留在 develop）
- [x] competition/{tasks,docs,deliverables} 三件套就位（deliverables 用 .gitkeep 占位）
- [x] knowevo/ 框架包 25 文件入库（含 docs/adr/0001-0008-已冻结架构决策.md；排除其自带 competition/ 避免台账双份分叉）
- [x] THIRD_PARTY_NOTICE.md 骨架就位（登记表 + 4 条纪律）
- [x] git status 仅显示预期新增项，无意外改动
**Evidence**:
- `git branch -v` → `develop 69e85c8`、`main 69e85c8`（同基线）
- `git status --short` → 仅 `?? THIRD_PARTY_NOTICE.md / ?? competition/ / ?? knowevo/`
- `git check-ignore` exit=1（未被 .gitignore 误伤）
- 目录树：`competition/deliverables/.gitkeep` + `competition/docs/{cost-ledger,evolution-log,pitfalls}.md` + `competition/tasks/README.md` + `knowevo/**` 25 文件
- 提交状态：**未提交**（改动在 develop 工作区，待用户确认后提交）
