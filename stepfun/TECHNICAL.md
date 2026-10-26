# 端侧推理与 seekdb 混合检索 · 技术说明

**模型与量化。** Xenova/all-MiniLM-L6-v2，revision `751bff37182d3f1213fa05d7196b954e230abad9`，384 维、mean pooling、L2 归一化。使用同一 revision 的 FP32、FP16 与 INT8 ONNX 文件，Transformers.js 3.8.1 显式选择 `dtype`。体积统一包含权重及四个共用 JSON 文件：FP32 **91.10 MB**、FP16 **46.01 MB**、INT8 **23.69 MB**，压缩比分别 **1.98× / 3.85×**；准确字节与 SHA-256 见 model-manifest.json。

**端侧运行。** 文档与查询都在浏览器 Web Worker 中计算，模型由本机 HTTP 静态服务提供。优先 `navigator.gpu.requestAdapter()` + `device: webgpu`，FP16 检查 shader-f16；无适配器或初始化失败自动使用 `wasm` + `q8`，界面显示真实后端及原因。单个 pipeline 复用，Worker 内请求串行；WASM 使用单线程，不要求跨源隔离。真实普通 HTTP 测试域中 `navigator.gpu` 不存在，完整自动降级评测通过。

**seekdb 数据层。** 官方 seekdb-js 1.4.0 server 模式连接 seekdb 1.4.0。Schema 含 384 维 cosine HNSW 与 beng 全文索引，embeddingFunction=null；保存 ID、原文、向量、标题和来源。hybridSearch 在数据库通过 DBMS_HYBRID_SEARCH 生成并执行全文 + 向量 + RRF 查询（候选各 20，rankConstant=60，最终 5），客户端只展示数据库排序。SHA-256 ID + upsert 支持幂等增量写入；导入最后一批调用 refresh_index。服务串行化共享连接上的请求，避免 SDK 会话变量 @search_parm 被并发请求覆盖。

**状态与优化。** 知识库名称与精度组合成集合名；页面保存选择，切换后查询明确指定当前集合。模型缓存、Web Worker、GPU 批大小 8 和合并索引刷新降低重复工作。实测 WASM 的批大小 8 比 1 慢，因此常规 WASM 导入使用批大小 1。所有性能比较均保留三轮，报告中位数。

**实测结果（2026-10-26）。** Apple M1 Pro、macOS 26.5.1、Chromium 155；固定 32 篇英文文档、12 条查询，每条一条相关文档。每种配置三轮查询，k=5。数据库在同机 Docker Linux arm64。小规模英文集合用于工程回归，不能代表通用语义质量。

| 配置 | 32 篇向量化 batch 1 / 8 (ms) | 查询向量化 (ms) | seekdb (ms) | 提交至展示 (ms) | Recall@5 / nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: |
| WebGPU FP32 | 308.2 / 60.7 | 12.2 | 10.5 | 33.2 | 1.000 / 0.908 |
| WebGPU FP16 | 318.0 / 49.0 | 11.1 | 5.9 | 21.6 | 1.000 / 0.908 |
| WASM FP32 | 519.9 / 553.6 | 9.6 | 10.6 | 34.9 | 1.000 / 0.908 |
| WASM INT8 | 603.7 / 655.1 | 12.1 | 24.2 | 52.6 | 1.000 / 0.908 |

量化后本集合质量变化为 0。FP16 的 GPU 批处理提升 **6.49×**；四批写入从每批刷新改为最终刷新，总耗时 **2789.9 → 830.8 ms**。INT8 体积明显减小，但此次 WASM 延迟没有改善。数据库耗时受当时负载影响，不能归因于模型精度。原始日志分别记录 loadMs、模型 HTTP 资源读取、首次查询、后续查询、写入及刷新；加载测量含缓存差异，不用作冷启动加速比。增量导入、新集合为空、切回后新文档可检索三项检查在四种配置中全部通过。
