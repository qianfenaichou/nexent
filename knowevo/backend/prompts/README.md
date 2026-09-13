# prompts/ —— KnowEvo 双语 Prompt 模板清单
**纪律**（03 计划 §2.5）: 一律 YAML 双语成对（`knowevo_xxx_en.yaml` / `knowevo_xxx_zh.yaml`），格式对齐 `backend/prompts/` 现有模板。变量用 `{{...}}` 占位。

## 模板清单（12 对）

| 文件名（_en/_zh 成对） | 用途 | 关键设计点（依据分册） |
|---|---|---|
| `knowevo_seed_nomination` | K1 阶段0 术语提名 | 每章 top-K=8-12 概念+别名+预期父类（02-K1 §2） |
| `knowevo_concept_proposal` | K1 阶段1 概念提名 | few-shot 3 例（跨域）；temp 0.2；输出含 evidence_span/rationale |
| `knowevo_schema_assembly` | K1 阶段4 schema 装配 | 大档；输入确认类集+证据段（02-K1 §2） |
| `knowevo_extraction` | K2 证据段抽取 | 本体摘要 15k 截断；输出强制 class_ref；EXTRACTED/INFERRED 标注规则（03-K2 §1.1） |
| `knowevo_align_adjudicate` | K2 对齐 LLM 裁决 | 双方证据摘要对比；输出 merge/new/split+理由（03-K2 §2） |
| `knowevo_router` | K3 路由分类 | few-shot 分类 {R,M,RM}；~200 token 预算（04-K3 §1.2） |
| `knowevo_hop_planning` | K3 跳计划 | 先出"要经过什么类型中间实体"计划（04-K3 §2.1） |
| `knowevo_path_scoring` | K3 路径打分 | 相关性+证据富度+矛盾信号（04-K3 §2.1） |
| `knowevo_decision_card` | K3 决策卡组装 | instructor schema；反事实仅 top-1；医疗免责声明插拔（04-K3 §3） |
| `knowevo_change_classify` | K5 变更分类 | 7 类变更+命题级 diff 要点抽取（06-K5 §1.1） |
| `knowevo_update_proposal` | K5 受影响面→提案 | trigger_source=standard_update 的提案生成（06-K5 §2.2） |
| `knowevo_lesson_reflect` | K7 教训抽取 | 👎 触发；输出带问题签名的结构化教训（07-K7 §4） |

## 内容原则
1. 双语对语义严格等价（en 为运行默认，zh 供评委审阅与政务域中文场景）；
2. few-shot 示例中的领域案例避免只用医疗（政务案例防模板过拟合，也为 T-15 复用预埋）；
3. 每模板头部 YAML 注释标用途+所属分册+档位（small/mid/large）+温度建议；
4. 路由与抽取类模板变更须同步回归对应评测（模板 hash 进 eval_run_t.config）。

## 验收锚点
- 12 对成对齐全、YAML lint 通过、变量占位无拼写漂移（CI 脚本对比 en/zh 变量集一致性）；
- T-04/T-06 首轮抽检后回填实际信噪比数据到各模板头部注释。
