# skill_template_service.py —— K6 Skill 工作流模板沉淀（L4）
**归属任务**: T-13 · 依赖: decision_service（轨迹数据）、skill_template_t（T-03）
**依据**: [备忘录 07-K6](../../../04-算法与架构决策备忘录/07-K6K7-模板沉淀与经验记忆.md)

## 职责
轨迹收集 → n-gram 模式统计 → 人工圈选服务端 → LLM 概括成模板 → 入库与 Nexent Skill 仓库同步 → 复用度量统计 → 检索注入。

## 接口冻结

```python
class SkillTemplateService:
    # ── STEP1: 模式统计 (确定性, 零 LLM) ─────────────────────
    async def collect_trajectories(self, since: datetime) -> list[Trajectory]:
        """Successful runs only: K4-judged correct AND trace>=0.8.
        {question_signature, route, skill_calls[], tool_calls[], card_quality}."""
    def mine_patterns(self, trajectories: list[Trajectory], n: range = range(2, 5)) -> list[Pattern]:
        """n-gram frequency; keep patterns with freq>=3 across >=2 distinct
        question signatures. Attach 2 representative trajectories each."""

    # ── STEP2/3: 圈选与概括 ──────────────────────────────────
    async def list_candidates(self) -> list[Pattern]:       # 人工圈选会话的数据源
    async def induce_template(self, pattern: Pattern) -> SkillTemplate:
        """Large-tier LLM: pattern + representative trajectories + parameterization
        guide → SKILL.md (frontmatter + body with {domain}/{task_type}/
        {relation_template}/{domain_rules} variables). schema per memo 07 §2."""

    # ── 入库与同步 ──────────────────────────────────────────
    async def publish(self, template: SkillTemplate) -> None:
        """skill_template_t (master) + render to Nexent Skill repository
        (instance, progressive disclosure). Master/instance split:
        re-render on master edit."""
    async def instantiate(self, template_id: UUID, variables: dict[str, str]) -> SkillInstance:
        """Cross-industry reuse: swap {domain}=government etc. (T-15 path)."""

    # ── 检索注入 (任务签名匹配) ──────────────────────────────
    async def match(self, question: str, top_k: int = 2) -> list[SkillInstance]:
        """Signature match via embedding (threshold 0.7) or edit distance.
        Inject as skill suggestion to entry Skill (progressive disclosure)."""

    # ── 复用度量 (备忘录 07 §3) ──────────────────────────────
    async def record_reuse(self, template_id: UUID, run_outcome: RunOutcome) -> None: ...
    async def reuse_stats(self) -> ReuseReport:
        """R (reuse count), S (success rate vs bare baseline), D (median edit
        distance). 答辩目标: R>=3, S>=baseline+5pp, D<=0.2."""
```

## 数据契约
- `SkillTemplate.body_md`：参数化 SKILL.md 母版；`variables`：四变量定义与合法值域；`source`：`{pattern, mined_from, induced_at}`。
- 模板命名：`<domain>-<task>-workflow`（如 `healthcare-drug-choice-workflow`）。

## 验收锚点
- `pytest test/backend/services/knowevo/test_skill_template_service.py -v`：n-gram 频率过滤、模板渲染变量替换、instantiate 跨行业、复用统计三指标。
- T-13 端到端：≥2 模板入库 + 1 次注入调用成功 + 复用统计面板出数。
