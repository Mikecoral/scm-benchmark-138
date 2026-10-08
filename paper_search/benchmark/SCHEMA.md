# scm-multiselect-v1

新增 `scm-multiselect-v2`：公开每题字段完全不变；私有每题必须包含 `classification`，其字段严格为 `taxonomy_version / categories / rationale`。taxonomy_version固定`scm-business-domain-v1`，categories为1–2个合法且不重复的业务类别ID，无主次；固定排序只为稳定导出。五类见最终数据的taxonomy.json或05分类提示词。v1继续可读，旧数据不自动补标。公开/私有文件schema_version必须一致。

当前带分类的最终19题位于`data/paper-pilot-19-v2/`；原v1保留。分类依据最终题目的核心决策和必要机制，仅用于本地分析，不进入API请求。双标签题可分别计入两类，因此分组样本数之和可能超过总题数；整体评分每题一次。业务分类不代表已校准推理能力维度。

03/04私有Markdown中的`Business Classification：{...}`必须是单行严格JSON。汇总器将全量分类记录导出为v2，全部无标签的历史输入仍导出v1；部分分类拒绝导出，防止无声漏标。

已有数据补标：独立agent按`prompt/05_business_domain_annotation.txt`只读取公开benchmark.json生成annotations.json，再使用`python3 -m paper_search.benchmark.classify --source SOURCE_DIR --annotations ANNOTATIONS_JSON --prompt PROMPT_FILE --out NEW_DIR --dataset-id NEW_ID --agent-id ACTUAL_AGENT_ID`。此任务属于既有结果后处理，单独留痕，不改写历史四阶段。

`benchmark.json` 是唯一公开评测输入；`answer_key.json` 是仅用于本地评分的私有文件。Python 3.10+，标准库即可。

机器可读约束：`benchmark.schema.json` / `answer_key.schema.json`（JSON Schema 2020-12）；跨文件ID一致性、题号归属和非空白文本由schema.py额外检查。

公开数据：

```json
{
  "schema_version": "scm-multiselect-v1",
  "dataset_id": "paper-pilot-19-v1",
  "purpose": "development_pilot",
  "items": [{
    "id": "P1-Q1",
    "paper_id": "P1",
    "type": "multiple_select",
    "stem": "完整场景、模型、条件和问题指令",
    "options": {"A": "选项内容", "B": "选项内容", "C": "选项内容", "D": "选项内容"}
  }]
}
```

私有数据具有相同 schema_version/dataset_id，其 items 为 `id / answer / verification_status / evidence_markdown / provenance`。answer 为1–3个不重复大写字母；顺序不影响评分。公共字段严格限制，不包含答案、证据、来源或难度标记。stem和options保留原Markdown/LaTeX文本，不转换或修改数学内容。

模型输出契约：`{"answer":["A","C"]}`。答案仅为格式示例。严格JSON，不允许代码围栏、解释、额外字段、重复键、重复选项、小写或空数组。不抽取思考文本中的字母。按集合完全一致计分，无部分分。空数据集可表示无READY产物，但评测拒绝空选题。

当前19题是开发试运行结果，未做独立专家验收或难度校准。JSON规范化不提高其科学核验等级。最终私有证据以第04阶段为准；第02阶段卡仍可能有后续纠错，不能替代最终oracle。

从其他已核Markdown导出：

```bash
python3 -m paper_search.benchmark.export --public /path/public.md --private /path/private.md --dataset-id my-run-v1 --out /path/new-data
```

通用pipeline汇总同时导出JSON和answer_summary.md。历史19题的结构化镜像位于`paper_search/benchmark/data/paper-pilot-19-v1/`；与原始最终Markdown的内容对应由 validation_report.json 记录。
