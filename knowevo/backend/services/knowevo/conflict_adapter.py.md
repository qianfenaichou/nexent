# conflict_adapter.py —— A4 生产接线适配层（加法 seam）
**归属任务**: A4 生产接线与 E10 真实语料（2026-09-30）· 依赖: `conflict_kernel`（内核契约）、`schemas.ConflictAdjudication`（冻结 wire 三元组）
**依据**: `competition/docs/tech-optimization-2026-09-28/a4-wire-assessment-2026-09-30.md`（接线评估唯一权威）+ `a4-conflict-design-2026-09-30.md` §7

## 职责

把图行（`kg_relation_t` + 文档溯源）映射成 `conflict_kernel.Fact`，跑冻结三层算法，并把裁决投影到 `schemas.ConflictAdjudication` 三元组。**纯映射 + 纯裁决**：零 DB、零 LLM、零 `graph_store.supersede`。默认不 import 本模块的调用方行为零变化。

## 接口冻结

```python
def polarity_value(rel_type: str, claim: str) -> str: ...
def value_from_claim(rel_type: str, claim: str) -> str: ...
    # P1 preferred Fact.value extractor (= polarity_value A+B). Claim stays
    # provenance. Raw-claim default on relation_row_to_fact over-reports
    # (E10 build tenant: 200 claim-identity pairs vs 38 true = 81.0%).
    # A+B heuristic (e10-input-prep §3.2): rel_type closed vocab + claim keywords
    # (neg wins). Returns "pos"|"neg"|"neutral". Real-corpus E10 value proxy.
    # NOT gold: complementary multi-claim sentences share polarity (no conflict).

def lineage_key_from_title(title: str) -> str: ...
    # 去掉标题末尾年份/版次括号段，得到谱系键（EVOLUTION vs SOURCE_AUTHORITY 分流）
def version_tag_from_title(title: str) -> str: ...
    # 标题内版次括号段；无则 ""

def relation_row_to_fact(row: Mapping[str, Any], *,
                         doc: Mapping[str, Any] | None = None,
                         value: str | None = None) -> Fact: ...
    # value 缺省 = row["claim"]（仅 kg_service CONTRA 身份测试用）；真实语料 E10 请显式传 polarity_value() 结果——claim 原文会把互补多主张误报为冲突
    # source_id = doc.id；source_group = lineage_key(title)；version = version_tag(title)
    # authority_level = doc.authority_level（坏值/缺省回退 3）
    # valid_at/invalid_at 半开区间原样；"infinity"/None → 开区间

def facts_from_relation_rows(rows, *, docs=None, values=None) -> list[Fact]: ...
    # docs/values 以 row["id"] 为键

def records_to_adjudications(records) -> list[ConflictAdjudication]: ...
    # ConflictAdjudicationRecord.to_wire() 或已是 mapping；未知对象跳过

def reconcile_relation_rows(rows, *, docs=None, values=None,
                            version_clock, authority_rank=None,
                            extraction_error_ids=None, prefer=None,
                            llm=None) -> ReconcileResult: ...
    # rows→facts→conflict_kernel.reconcile；默认 llm=None 绝不调用

def make_relation_conflict_observer(*, docs=None, version_clock,
                                    authority_rank=None,
                                    on_records=None) -> Callable[[Mapping], None]: ...
    # 供 KGService(conflict_observer=...) 注入；只观察不改 merge 结局
```

## 生产注入点（加法，默认零变化）

| 注入点 | 签名增量 | 默认行为 |
|---|---|---|
| `KGService.__init__` | 尾参 `conflict_observer: Any \| None = None` | None = 与旧版逐字节同路径 |
| `KGService._merge_edge` | 冲突分支后 `_notify_conflict_observer` | observer 异常 debug 吞掉，不翻转 supersede/contested |
| `DecisionService.render_card` | 尾参 `conflict_records: list[ConflictAdjudication] \| None = None` | None = 冻结行为；有值则按 `conflict_id` 去重后并入 `card.conflict_adjudications`，LLM 回显同 id 不双计 |

## 数据契约

- 入：`kg_relation_t` 行投影 `{id, src, dst, rel_type, claim, valid_at, invalid_at}` + `doc_asset_t` 投影 `{id, title, authority_level}`。
- 出：`schemas.ConflictAdjudication{conflict_id, type, resolution}`（冻结三元组，`type` ∈ 内核四值词表）。
- `value` 必须由调用方填：本适配层缺省用 `claim`；两侧皆空 claim 不构成冲突（内核契约）。

## 验收锚点

- `pytest test/backend/services/knowevo/test_conflict_adapter.py -v`：行→Fact 映射 / 标题谱系与版次 / 三元组投影 / reconcile 门与顺序无关 / observer 仅冲突触发且异常不翻转 merge / render_card 合并去重与拒绝路径仍携带记录。
- 默认零变化：`test_kg_service.py` / `test_decision_service.py` 全量不改期望即绿。
