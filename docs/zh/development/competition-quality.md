# 证据选择与 PDF 检索的质量验证

公开行为测试与质量评测分别验证不同问题：citation 可以正确回查，并不意味着它指向问题所需的证据。不能用公开测试的通过数代替召回率。

## 实现

PDF 检索先根据文档标识和问题定位公司及报告年份。问题中的简称可从报告正文识别，同一公司的不同年份仍单独选择。页内排名不使用每页重复的文档 ID，避免短页因重复公司名、年份而得到过高分数。排序使用 BM25、通用财务术语扩展及财务报表标题。返回值仍是当前 scoped Memory 公开 `list` 接口提供的原有 citation，没有文档到答案页的映射。

证据选择保留原文及精确字符区间，增加低权重派生词匹配和结果句信号。选择器从多个可容纳的句子开始，按新增证据分数与实际增量字节成本补充片段。相邻原文区间合并后共用引用，不同 scope 的身份保持独立。所有输出开销都计入 UTF-8 预算。两项实现均无在线模型调用。

## SciFact 实测

数据来自 [SciFact 官方公开数据](https://github.com/allenai/scifact/tree/68b98a56d93e0f9da0d2aab4e6c3294699a0f72e)。离线评估器用标签构造一篇相关摘要和两篇词法相近的干扰摘要，再将三篇原文真实写入 Memory。产品选择器仅接收 query 和候选，不接收标签。

dev 集有 188 个带证据的 claim，分别执行 1,400 和 1,800 字节预算，共 376 次请求。多个证据集合取全部标注句的并集；合并相邻区间后，322 次请求可容纳完整证据和真实引用。这对应题面“完整证据可以装入预算”的前提。

| 真实 Memory / dev | 初始提交 `5a5b0e50` | 当前实现 |
| --- | ---: | ---: |
| 全部请求平均词项召回 | 57.36% | 64.00% |
| 全部请求完整覆盖比例 | 34.04% | 46.01% |
| 322 次预算可行请求平均词项召回 | 60.29% | **67.45%** |
| 预算可行请求完整覆盖比例 | 39.75% | **53.42%** |

题面门槛分别为 65% 和 40%。这不是平台的 24 个 claim / 48 次请求，不能保证平台得分；包含预算不可行请求的全量结果也如实列出。

```bash
PYTHONPATH=src uv run python scripts/evaluate_evidence_quality.py \
  --data /path/to/scifact/data --split dev --output /tmp/evidence-quality.json
```

数据目录应包含 `corpus.jsonl` 和 `claims_dev.jsonl`，也可选择 `--split train`。评估脚本和标签不会被产品源码加载。

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

- 官方固定依赖 Docker 自测：PDF 6 项、证据选择 3 项通过。
- 产品回归：4 个新增场景和 71 个普通上下文测试通过，覆盖派生词、过长候选、缩写断句、报表定位和简称跨年份选择。
- 初始实现会在新增的派生词、财务报表定位测试上失败，当前实现通过。
- `competition/` 测试、样例和依赖保持不变。产品源码不读标签、答案或证据页码。

评估日期：2026-10-26。数据 SHA-256：

```text
SciFact data.tar.gz
11c621288d41ac144d29b13b0f8503b3820b7d6e8b1f6ff24dff335c196d76be
FinanceBench financebench_open_source.jsonl
a5a2aa673e573e55675fc3c0f9aa38c1cf59d2abc91edb077534f71f10a71877
```

各数据及公司原财报保留原许可。本仓库只提供评估工具，不重新分发数据。
