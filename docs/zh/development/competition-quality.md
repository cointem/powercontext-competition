# 证据选择与 PDF 检索的质量验证

公开行为测试与质量评测分别验证不同问题：citation 可以正确回查，并不意味着它指向问题所需的证据。不能用公开测试的通过数代替召回率。

## 实现

PDF 检索先根据文档标识和问题定位公司及报告年份。问题中的简称可从报告正文识别，同一公司的不同年份仍单独选择。页内排名不使用每页重复的文档 ID，避免短页因重复公司名、年份而得到过高分数。排序使用 BM25、通用财务术语扩展及财务报表标题。返回值仍是当前 scoped Memory 公开 `list` 接口提供的原有 citation，没有文档到答案页的映射。

证据选择保留原文及精确字符区间，使用查询词项、文档频率、派生词匹配和摘要结构排序。较长摘要的开头及背景、方法段降权，结果、发现和结论段的信号延续到后续句子，减少标题和重复关键词挤占证据预算的情况。

字节预算通过动态规划分配，区分新建引用和延长原有区间的成本。优先保留完整语句；没有完整相关语句可容纳时，长段落按原文词边界及必要的字符边界拆分，再选择查询相关窗口。中文按句末标点断句，并使用相邻汉字匹配句内短语。片段不经过改写；尾部空白、换行、精确身份和字符区间均保留。trust policy 与普通 prepare 使用相同的分隔格式。所有引用、JSON 转义和 UTF-8 输出开销均计入预算。两项实现均无在线模型调用。

## SciFact 实测

数据来自 [SciFact 官方公开数据](https://github.com/allenai/scifact/tree/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e)。离线评估器用标签构造一篇相关摘要和两篇词法相近的干扰摘要，再执行官方 runner 使用的 `capture → flush → search(fts) → build_scopes_result` 路径。产品只接收 query 和候选正文，不读取标签。检索未召回目标摘要的请求仍计入指标分母。

公开样例把标题和摘要拼接后入库，因此默认评估这种格式，并保留纯摘要输入对照。标题也参与检索及选择，但不属于标注证据；标注字符区间相应增加标题长度。每个 claim 执行 1,400 和 1,800 字节两档预算。多个证据集合取全部标注句的并集；预算可行性根据完整证据、真实引用及完整 envelope 独立判断。

| 数据集与输入格式 | 范围 | 请求数 | 平均词项召回 | 完整覆盖比例 |
| --- | --- | ---: | ---: | ---: |
| dev / 标题＋摘要 | 全部 | 376 | **69.37%** | **52.66%** |
| dev / 标题＋摘要 | 预算可行 | 322 | **72.75%** | **60.87%** |
| train / 标题＋摘要 | 全部 | 1,010 | **70.52%** | **51.78%** |
| train / 标题＋摘要 | 预算可行 | 861 | **73.52%** | **59.58%** |
| dev / 纯摘要 | 全部 | 376 | **69.64%** | **52.93%** |
| dev / 纯摘要 | 预算可行 | 322 | **72.74%** | **61.18%** |

同样的 dev 标题＋摘要输入下，提交 `9ad7e1c8` 的预算可行召回率为 68.16%、完整覆盖率为 52.48%；当前实现分别增加 4.59 和 8.39 个百分点。train 测量在本次算法确定后执行；产品没有训练模型或读取数据集文件。

另用官方固定依赖 Docker 中的原始 runner，执行 dev 顺序中前 24 个在两档预算下均可行的 claim，共 48 次请求：召回率 89.43%，完整覆盖率 79.17%。每个片段都经过真实 citation 回查、原文区间、origins 和完整 UTF-8 预算校验。这是可复现的本地样本选择，不是平台隐藏的 24 个 claim。

小样本结果存在波动：从 dev 中 145 个两档预算均可行的 claim 随机选 24 个（随机种子 `20261027`，1,000 组），同时达到 65% 召回和 40% 完整覆盖的比例从对照提交的 74.4% 提高到 89.4%。该比例不代表平台通过概率，也说明全量均值超过门槛仍不能保证未知子集通过。没有平台日志，不能断言其此前失败的具体原因。

```bash
PYTHONPATH=src uv run python scripts/evaluate_evidence_quality.py \
  --data /path/to/scifact/data --split dev --document-format title-abstract \
  --output /tmp/evidence-title-quality.json

PYTHONPATH=src uv run python scripts/evaluate_evidence_quality.py \
  --data /path/to/scifact/data --split dev --document-format abstract \
  --output /tmp/evidence-abstract-quality.json
```

数据目录应包含 `corpus.jsonl` 和对应的 `claims_dev.jsonl` 或 `claims_train.jsonl`。脚本、标签均不会被产品源码加载。结果 JSON 包含每次请求的预算、召回、完整覆盖、候选数和目标是否被检索召回。

## FinanceBench 实测

使用 [FinanceBench 固定公开版本](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)的 13 份完整原 PDF，共 2,452 页、52 个问题。在同一个文档集合中检索，按不同证据页数计数，共命中 **55 / 64 页，Recall@20 为 85.94%**。

| 报告 | 命中 / 标注页数 | Recall@20 |
| --- | ---: | ---: |
| 3M 2018 10-K | 2 / 2 | 100% |
| Adobe 2022 10-K | 2 / 2 | 100% |
| AES 2022 10-K | 5 / 5 | 100% |
| Amcor 2023 10-K | 4 / 4 | 100% |
| AMD 2022 10-K | 6 / 7 | 85.71% |
| American Express 2022 10-K | 5 / 7 | 71.43% |
| Best Buy 2023 10-K | 3 / 3 | 100% |
| Boeing 2022 10-K | 7 / 9 | 77.78% |
| CVS Health 2022 10-K | 5 / 5 | 100% |
| Johnson & Johnson 2022 10-K | 4 / 4 | 100% |
| PepsiCo 2022 10-K | 6 / 8 | 75% |
| Pfizer 2021 10-K | 3 / 4 | 75% |
| Verizon 2022 10-K | 3 / 4 | 75% |

各组均超过题面 70% 的页召回门槛。标注证据文本召回率最低为 96.45%，标注整页词项 F1 最低为 95.81%。PDF 仍完整解析全部页面，没有使用预解析答案正文。

质量脚本使用轻量 Memory `list` 适配器隔离测量解析及排序；真实存储、重启和 citation 回查由官方 Docker 测试及产品回归测试另行验证。平台冻结的逐页词项计数和隐藏文档划分不可见，本地不声称已通过该项官方验收。

```bash
PYTHONPATH=src uv run --extra pdf python scripts/evaluate_pdf_quality.py \
  --data /path/to/financebench_open_source.jsonl \
  --pdf-dir /path/to/selected/pdfs --output /tmp/pdf-quality.json
```

PDF 文件名应为原始文档 ID，例如 `PEPSICO_2022_10K.pdf`。脚本衡量所提供文档对应的公开问题，并将逐项结果保存为 JSON。

## 行为验证与数据边界

- 官方固定依赖 Docker 自测：当前证据选择 3 项通过；PDF 实现保持此前 6 项通过的版本。
- 产品回归：10 个检索质量场景和 71 个普通上下文测试通过，覆盖派生词、过长候选、缩写断句、尾部空白、跨句合并、Unicode/JSON 字节预算、artifact 身份、长段落窗口、中文短语、标题与正文竞争，以及报表定位和简称跨年份选择。
- 提交 `9ad7e1c8` 在长段落窗口、中文短语和标题与结果句竞争三个回归场景上失败，当前实现通过。长段落场景另外通过官方 Docker runner 的真实入库、检索及 citation 回查。
- `competition/` 测试、样例和依赖保持不变。产品源码不读标签、答案或证据页码。

证据选择评估日期：2026-10-27；PDF 评估日期：2026-10-26。数据 SHA-256：

```text
SciFact data.tar.gz
11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be
FinanceBench financebench_open_source.jsonl
a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877
```

各数据及公司原财报保留原许可。本仓库只提供评估工具，不重新分发数据。
