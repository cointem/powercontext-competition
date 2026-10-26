# 拾页 · 浏览器本地知识库

阶跃星辰赛题：文档与查询在浏览器中生成向量，通过官方 **seekdb-js** 保存与检索。支持文本导入、增量更新、知识库切换、WebGPU / WASM 自动选择，以及可运行的量化对比评测。不需要 API Key。

## 运行

需要 Node.js 22+、Python 3、curl、Docker Compose。实测 Node.js 24.20.0、macOS 26.5.1、Apple M1 Pro、Chromium 155。使用近期 Chrome / Edge 打开。

```sh
docker compose up -d --wait
npm ci --ignore-scripts
npm run models
npm run build
npm start
```

打开 **http://127.0.0.1:15174/**。Compose 在 12882 端口启动独立 seekdb 1.4.0，数据保存在命名卷中。首次下载约 159 MB 的三个公开模型权重及分词文件；模型文件不提交到 Git。后续启动无需重新下载。服务器只负责静态文件和数据库操作，未注册任何服务端 embedding function。

已有 seekdb 时可以跳过 Compose，通过 `SEEKDB_HOST`、`SEEKDB_PORT`、`SEEKDB_USER`、`SEEKDB_PASSWORD`、`SEEKDB_DATABASE` 配置。默认数据库为 `stepfun_browser_kb`，自动创建。HTTP 端口可用 `PORT` 指定。开发方式为两个终端分别运行 `npm run server` 与 `npm run dev`。

`.npmrc` 关闭 peer 自动安装，是因为本应用无需 `@seekdb/default-embed`；该包的部分发布版本包含 `workspace:*` peer。`--ignore-scripts` 跳过未使用的 Node 推理与嵌入式原生插件安装脚本，浏览器和 MySQL server 模式不依赖它们。

## 试玩

1. 保持“自动 · 优先 WebGPU”，点击“导入示例语料”。页面显示实际后端、精度、进度以及写入和刷新耗时。
2. 点击“检索知识库”。默认查询应将 **Solar power** 排在第一位，结果来自 seekdb 的全文与向量混合检索。
3. 粘贴新内容或选择 TXT / Markdown，再次导入。按段落与长度切片，文档 ID 由来源、标题、片段的 SHA-256 生成；重复导入相同内容会 upsert。
4. 修改知识库名称并导入另一批内容，或从“已有知识库”选择；刷新页面保留选择。不同精度的集合使用独立后缀，避免写入时混用。设备降级后仍可用同一模型的查询向量检索已选集合；继续导入需选择与存储精度一致的配置或新建库。
5. 选择“仅语义向量”可与数据库混合检索对照。正常使用无需打开评测。

MiniLM 是英文语义模型，随附评测语料为英文。中文文本也可导入与全文匹配，但中文语义质量不在本次评测范围内。

## 复现评测

点击“运行当前后端评测”。独立 `kb_bench_*` 集合保存固定的 32 篇原创文档和 12 条人工相关性标注查询。每组执行三轮，记录 FP32 与 FP16（WebGPU）或 INT8（WASM）的 Recall@5、nDCG@5、逐阶段计时、批处理、刷新策略，以及增量 / 集合切换检查。点击“导出 JSON”下载全部原始记录；后端也保存到 `evidence/runtime/`。

- **WebGPU**：使用默认 localhost 地址，“自动”或“仅 WebGPU”。
- **WASM**：选择“WASM 兼容模式”。
- **真实自动降级**：本次还使用 `http://127.0.0.1.nip.io:15174/`（DNS 指向本机）测试。“自动”配置下，该普通 HTTP 域名的 `navigator.gpu` 不可用，应用自动采用 WASM + INT8。报告的 `gpuAvailable: false` 与 `backendProbe.reason` 记录了这个条件。正常使用仍推荐 localhost。

原始实测见 `evidence/webgpu.json` 与 `evidence/wasm-fallback.json`，结果摘要见一页技术说明 `TECHNICAL.md`。所有指标都从本应用执行获得；未用服务端向量或检索结果回放。模型加载记录带有资源传输大小，可区分新读取与缓存命中。CPU / GPU 结果按各自环境报告，不将跨环境差异直接视作量化收益。

API 集成测试使用真实 seekdb 和明确标注的合成向量，专门检查数据层：

```sh
node --test test-api.mjs
```

测试覆盖重复导入、增量索引刷新、12 个并发混合请求的集合隔离与无效输入。模型与检索质量测试在浏览器中运行。测试集合保留在专用数据库中，可用于检查；正常知识库列表隐藏评测集合。

## 文件与实现

| 文件 | 职责 |
| --- | --- |
| `src/embedding.worker.js` | Transformers.js 3.8.1 本地推理、显式后端、模型复用、降级 |
| `src/main.js` | 导入、查询、会话选择、可运行评测及指标采集 |
| `src/corpus.js` | 固定语料与查询标注 |
| `server.mjs` | seekdb-js 1.4.0，集合、upsert、refresh_index、hybridSearch |
| `scripts/download_model.py` | 固定模型 revision 下载与 SHA-256 / 字节体积记录 |
| `public/model-manifest.json` | 模型来源、版本、各文件的实测大小与校验值 |

模型来自 [Xenova/all-MiniLM-L6-v2](https://huggingface.co/Xenova/all-MiniLM-L6-v2/tree/751bff37182d3f1213fa05d7196b954e230abad9)，Apache-2.0。直接选用上游预转换的 FP32、FP16、8 位量化 ONNX 版本；下载脚本固定全部文件版本，运行时通过 `dtype` 选择，不在浏览器中临时量化。数据库接口依据 [seekdb-js 官方源码](https://github.com/oceanbase/seekdb-js)，浏览器模型执行方式依据 [Transformers.js 官方说明](https://huggingface.co/docs/transformers.js/v3.0.0/en/index)。应用、评测语料与脚本由本项目编写。
