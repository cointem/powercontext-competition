# 证据选择与 PDF 检索的质量验证

公开行为测试与质量评测分别验证不同问题：citation 可以正确回查，并不意味着它指向问题所需的证据。不能用公开测试的通过数代替召回率。

## 实现

PDF 检索先根据文档标识和问题定位公司及报告年份。问题中的简称可从报告正文识别，同一公司的不同年份仍单独选择。页内排名不使用每页重复的文档 ID，避免短页因重复公司名、年份而得到过高分数。排序使用 BM25、通用财务术语扩展及财务报表标题。返回值仍是当前 scoped Memory 公开 `list` 接口提供的原有 citation，没有文档到答案页的映射。

证据选择保留原文及精确字符区间，使用查询词项、文档频率、低权重派生词匹配和结果句信号排序。字节预算通过动态规划分配，区分新建引用和延长原有区间的成本；连续原文可以跨过没有查询词的句子，从而共用引用并保留解释上下文。剩余预算用于补充相邻原文单词。断句保留尾部空白和换行，避免完整证据的字符区间被截断。所有输出开销都计入 UTF-8 预算，不同 scope、artifact、revision 和 entry version 的身份保持独立。两项实现均无在线模型调用。

## SciFact 实测

数据来自 [SciFact 官方公开数据](https://github.com/allenai/scifact/tree/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e)。离线评估器用标签构造一篇相关摘要和两篇词法相近的干扰摘要，再按官方 runner 的路径执行 `capture → flush → search(fts) → build_scopes_result`。产品选择器仅接收 query 和检索后的候选，不接收标签。检索未召回目标摘要的请求仍计入指标分母。

每个 claim 分别执行 1,400 和 1,800 字节预算。多个证据集合取全部标注句的并集；合并相邻区间后，检查完整证据与真实引用是否可以装入预算。预算可行子集对应题面的可行性前提，同时列出所有请求结果。

| 数据集与范围 | 请求数 | 平均词项召回 | 完整覆盖比例 |
| --- | ---: | ---: | ---: |
| dev 全部（188 个 claim） | 376 | **67.07%** | **50.00%** |
| dev 预算可行 | 322 | **70.37%** | **57.45%** |
| train 全部（505 个 claim） | 1,010 | **69.04%** | **50.79%** |
| train 预算可行 | 861 | **72.74%** | **58.30%** |

train 数据在确定本次算法后用于验证，未用来拟合参数。产品未训练模型，也不读取任何数据集文件。dev 同一路径的对照结果：提交 `5df84ad8` 在 322 次预算可行请求上的召回率为 66.41%，完整覆盖率为 52.48%。

题面门槛分别为 65% 和 40%。上述请求不是平台的 24 个 claim / 48 次请求，因此不能替代正式质量验收，也不能据此断定平台此前失败的具体原因。

```bash
PYTHONPATH=src uv run python scripts/evaluate_evidence_quality.py \
  --data /path/to/scifact/data --split dev --output /tmp/evidence-quality.json
```

数据目录应包含 `corpus.jsonl` 和 `claims_dev.jsonl`，也可选择 `--split train`。评估脚本和标签不会被产品源码加载。结果 JSON 包含每次请求的预算、召回、完整覆盖、候选数和目标是否被检索召回，便于区分召回阶段和片段选择阶段的问题。

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
- 产品回归：7 个检索质量场景和 71 个普通上下文测试通过，覆盖派生词、过长候选、缩写断句、尾部空白、跨句合并、Unicode/JSON 字节预算、artifact 身份，以及报表定位和简称跨年份选择。
- 提交 `5df84ad8` 在尾部空白与跨句合并两个回归场景上失败，当前实现通过。
- `competition/` 测试、样例和依赖保持不变。产品源码不读标签、答案或证据页码。

评估日期：2026-10-26。数据 SHA-256：

```text
SciFact data.tar.gz
11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be
FinanceBench financebench_open_source.jsonl
a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877
```

各数据及公司原财报保留原许可。本仓库只提供评估工具，不重新分发数据。
