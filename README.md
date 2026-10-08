# SCM Benchmark 138：供应链决策多选题评测

固定题集版本：`utd-30-papers-138-v1`。本仓库包含 **30 篇论文对应的 138 道中文多选题**、标准答案以及独立模型请求与评分程序，供合作评测使用。导出日期：2026-10-08。

本题集沿用原始数据的 `purpose: development_pilot`，不是训练/验证划分中的 35 题验证集。READY 表示生成与核验流程中的通过状态，不表示独立专家认可或经过难度标定。仓库不提供模型排行榜或未经验证的模型成绩。

## 1. 文件与数据边界

- `paper_search/benchmark/data/utd-30-papers-138-v1/benchmark.json`：公开题目，包含题号、论文编号、题干及 A–D 选项，不含答案。
- 同目录 `answer_key.json`：评分答案，另含证据、来源和私有业务分类；仅用于评分，不得送入模型、RAG 或作为解题上下文。
- 同目录 `ready_questions_public.md`：可阅读的题面；`paper_index.csv`、`run_summary.md`、`taxonomy.json`：题集索引与业务分类说明。
- `paper_search/benchmark/schema.py`：校验、题面渲染与固定 system prompt。
- `paper_search/evaluation/`：请求、断点恢复、答案提取、报告与多模型比较程序。
- `PROMPTS.md`：两种协议的完整 prompt 原文。
- `release_manifest.json`：题集版本、数量及随包文件 SHA-256。

每题是 A–D 多选，正确答案包含 1–3 个选项。每题独立发送 system + user，请求只含该题题干与选项；不发送其他题、标准答案、原论文或证据。原始题目、答案和评测核心代码从现有工程原样复制。

## 2. 环境与下载

需要 Python **3.10+**。评测程序只使用标准库，无需额外 pip 安装。API 调用的费用由评测者自己的账号承担。兼容 OpenAI Chat Completions 接口；本仓库不负责启动本地推理服务。

通过 GitHub 的 **Code → Download ZIP** 下载并解压，或使用页面给出的 clone 地址。随后进入仓库根目录，所有命令都在根目录运行。

```bash
python3 --version
cp paper_search/evaluation/providers.example.json paper_search/evaluation/providers.local.json
```

Windows 可将命令中的 `python3` 改成 `python`，复制文件可用文件管理器。下面反斜杠续行示例适用于 Bash/Zsh；Windows 可合并成一行运行。

## 3. 配置自己的模型

编辑 `providers.local.json`，修改所选 profile 的 `base_url` 和 `model`。示例中的 `YOUR_API_HOST` / `YOUR_MODEL_ID` 是必须替换的占位符，不能直接调用。`base_url` 是 API 基地址，程序会追加 `/chat/completions`。

- `model-thinking`：思考模式或未显式关闭，使用 `answer-json-v1`。示例不主动开启厂商思考开关，请依据目标接口设置，例如 `extra_body.enable_thinking: true` 或 `extra_body.thinking.type: enabled`。
- `model-no-thinking`：示例使用 `extra_body.enable_thinking: false`，触发 `discussion-answer-json-v2`。厂商参数不同时，替换为接口支持的开关，例如 `thinking: {"type": "disabled"}`、`reasoning_effort: "none"`，或 vLLM 的 `chat_template_kwargs: {"enable_thinking": false}`。不要同时保留目标接口不支持的开关。

`temperature` 可设为 `null` 以省略该参数。`max_tokens`、超时、流式选项与并发须按目标模型能力和双方约定设置；示例预算为 32768、并发为 4，不代表所有模型的推荐值或原实验的统一设置。默认 `json_mode: false`；仅在目标接口支持且使用 `answer-json-v1` 时考虑开启。讨论协议会省略 `response_format`。

配置仅填写密钥的**环境变量名**，不要写密钥本体。Bash/Zsh 可用以下方式隐藏输入并设置变量：

```bash
read -r -s BENCHMARK_API_KEY
export BENCHMARK_API_KEY
```

执行第一行后输入自己的 API 密钥并回车。PowerShell 可通过自己使用的密钥管理方式设置 `$env:BENCHMARK_API_KEY`。程序不会自动加载 `.env` 文件。本地配置、密钥文件和评测输出已加入 `.gitignore`。

## 4. 离线检查（不联网、不扣费）

```bash
python3 -m paper_search.evaluation.run \
  --dataset paper_search/benchmark/data/utd-30-papers-138-v1/benchmark.json \
  --answers paper_search/benchmark/data/utd-30-papers-138-v1/answer_key.json \
  --config paper_search/evaluation/providers.local.json \
  --profile model-no-thinking \
  --out output/evaluations/check \
  --dry-run
```

应显示 `DRY RUN: 138 independent requests`。dry-run 校验数据和配置、构造请求，不验证真实接口权限、参数支持或模型可用性，也不创建结果目录。

## 5. 正式评测

先执行单题接口检查，结果放在专门的 smoke 目录：

```bash
python3 -m paper_search.evaluation.run \
  --dataset paper_search/benchmark/data/utd-30-papers-138-v1/benchmark.json \
  --answers paper_search/benchmark/data/utd-30-papers-138-v1/answer_key.json \
  --config paper_search/evaluation/providers.local.json \
  --profile model-no-thinking \
  --out output/evaluations/model-no-thinking-smoke \
  --limit 1
```

检查输出中的错误状态和实际回答，再运行完整 138 题：

```bash
python3 -m paper_search.evaluation.run \
  --dataset paper_search/benchmark/data/utd-30-papers-138-v1/benchmark.json \
  --answers paper_search/benchmark/data/utd-30-papers-138-v1/answer_key.json \
  --config paper_search/evaluation/providers.local.json \
  --profile model-no-thinking \
  --out output/evaluations/model-no-thinking-138
```

思考模式改成 `--profile model-thinking` 并使用新的输出目录。每个模型、模式和参数设置使用独立的新目录，不能把单题检查目录当完整运行续跑。每题只产生一个正式预测；瞬时 API 故障重试不等于多次采样择优。

### 断点续跑与错误补跑

完整运行中断后，使用**原完整命令**追加 `--resume`。已记录题目不会再次请求，包括已记录的 API 错误。需要补跑 API 错误时追加 `--resume --retry-errors`，该操作会再次调用 API。

程序校验题集、答案、选题、模型、生成参数和 prompt 身份，发生变化时拒绝恢复；允许仅改变并发并记录在 manifest。不要对同一目录同时启动多个写入进程。

示例 `retries: 2` 表示每次运行每题首次调用后最多 2 次瞬时错误重试，仅重试 408/429/指定 5xx 或网络错误；不会因答错、格式错误或截断重试，不会自动修改模型参数。

## 6. 输出协议与评分

完整 system prompt 见 [PROMPTS.md](PROMPTS.md)，程序以 `schema.py` 为准。

- `answer-json-v1`：最终 content 只输出一个 JSON，例如 `{"answer":["A","C"]}`。
- `discussion-answer-json-v2`：按 A、B、C、D 顺序分别简要说明选项依据，必要时给短计算，末尾输出唯一答案 JSON；不要引入题外假设，不在分析中输出候选答案 JSON。

自动识别显式关闭思考的配置开关；未显式关闭时使用 `answer-json-v1`。如配置 `system_prompt_suffix`，追加内容会进入实际 prompt 和 manifest；比较实验前应明确约定该内容。

主评分协议为 **`answer-extraction-v2`**：从正常完成的最终 content 中提取唯一合法答案 JSON，支持围栏和前后说明。答案按选项集合完全匹配，漏选、多选或错选均为错误，没有部分分。重复选项、越界选项、空数组、多个或嵌套答案对象不会作为有效答案评分。截断、API 错误、仅 reasoning 中出现答案等不会被恢复为有效答案。

- **`accuracy_all`（主指标）** = 正确题数 / 138，完整运行中错误、缺失、格式失败、API 错误和截断都包含在分母。
- `accuracy_valid` = 正确题数 / 有效最终答案数，是辅助指标；应同时报告有效答案率，不要用它替代主指标。
- `output_format_compliant` 是独立结构检查。非思考模式检查 A–D 顺序、各段非空、答案末尾位置及选项排序；它不评价分析质量。答案正确但未逐项分析，可以计为正确，同时格式不合规。

`finish_reason=length` 为截断；缺少正常 stop 不假定成功完成。评分状态包括 `correct`、`incorrect`、`api_error`、`truncated`、`empty`、`non_answer_finish`、`invalid_json`、`invalid_answer`、`missing`。

## 7. 结果回传

完整结果目录包含：

| 文件 | 内容 |
|---|---|
| `manifest.json` | 数据/答案哈希、选题 ID、profile 参数、完整 prompt 与请求协议 |
| `responses.jsonl` | 请求正文、最终 content、reasoning、模型 ID、finish_reason、usage、耗时、各次尝试及错误 |
| `report.json` | 正式评分、准确率和逐题结果 |
| `results.csv` | 便于表格查看的逐题摘要 |
| `summary.md` | 简洁汇总和答案对照 |
| `report.html` | 可离线打开的交互报告 |

请将**整个该模型结果目录**压缩后回传，至少包含前三项，并说明仓库 commit、模型版本、思考模式、运行日期及任何额外推理设置。不要回传密钥或本地凭据配置。日志保留请求和响应，标准环境变量配置不会保存 Authorization 请求头。

离线复评分可使用原完整命令，将 `--out` 改为新目录，并追加 `--offline /path/to/responses.jsonl`；无需密钥，也不会调用 API。必须使用与原运行相同的数据和 profile 参数，且不能混入其他模型或题集的响应。

### 多模型比较

```bash
python3 -m paper_search.evaluation.compare \
  --runs output/evaluations/run-a output/evaluations/run-b \
  --out output/comparison
```

比较程序要求数据、答案、选题、system prompt 和评分协议一致。思考与非思考使用不同 prompt，需分别报告，不能直接合并进此比较。预算和模式要随成绩一起披露。

## 8. 版本与使用说明

`release_manifest.json` 记录随包文件的 SHA-256；请保留数据原文件和题号，评测时不要修改题干或标准答案。标准答案随仓库提供意味着本发布包适用于合作方自行评分，不是答案隐藏的盲测提交系统。

本仓库不附原论文 PDF、训练数据、训练脚本、原始出题工作目录或已有模型回答。此发布尚未指定开放再分发许可；需要公开再分发或更换许可时，请联系仓库维护者确认。
