import { pipeline, env } from '@huggingface/transformers';
env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.backends.onnx.wasm.numThreads = 1;
let extractor, activeKey, actual, loadMs;

async function load(options) {
  const key = `${options.backend}:${options.dtype}`;
  if (extractor && activeKey === key) return { ...actual, loadMs: 0, reused: true };
  if (extractor) { await extractor.dispose(); extractor = undefined; }
  let device = 'wasm', reason = '', dtype = options.dtype;
  if (options.backend !== 'wasm') {
    try {
      const adapter = await navigator.gpu?.requestAdapter();
      if (adapter && (dtype !== 'fp16' || adapter.features.has('shader-f16'))) device = 'webgpu';
      else reason = adapter ? 'WebGPU 缺少 shader-f16' : 'WebGPU 不可用';
    } catch (error) { reason = error.message; }
    if (options.backend === 'webgpu' && device !== 'webgpu') throw new Error(reason);
  } else reason = '已选择 WASM 兼容模式';
  if (device === 'wasm' && dtype === 'fp16') dtype = 'q8';
  const t = performance.now();
  performance.clearResourceTimings();
  const create = () => pipeline('feature-extraction', 'Xenova/all-MiniLM-L6-v2', {
    dtype, device, progress_callback: event => self.postMessage({ progress: event }),
  });
  try { extractor = await create(); }
  catch (error) {
    if (options.backend !== 'auto' || device !== 'webgpu') throw error;
    reason = `WebGPU 初始化失败，自动降级：${error.message}`;
    device = 'wasm'; dtype = dtype === 'fp32' ? 'fp32' : 'q8';
    extractor = await create();
  }
  loadMs = performance.now() - t;
  actual = { backend: device, dtype, reason, threads: 1 };
  activeKey = key;
  return { ...actual, loadMs, reused: false, resources: performance.getEntriesByType('resource')
    .filter(r => r.name.includes('/models/')).map(r => ({url: r.name, durationMs: r.duration, transferBytes: r.transferSize, decodedBytes: r.decodedBodySize})) };
}

// Serialize requests because a pipeline instance may not be invoked concurrently.
let queue = Promise.resolve();
self.onmessage = ({ data }) => {
  queue = queue.then(async () => {
    try {
      const info = await load(data.options);
      if (!data.texts) { self.postMessage({ id: data.id, info }); return; }
      const batchSize = data.batchSize ?? (info.backend === 'webgpu' ? 8 : 1);
      if (!Number.isInteger(batchSize) || batchSize < 1) throw new Error('批大小必须为正整数');
      const vectors = [], t = performance.now();
      for (let i = 0; i < data.texts.length; i += batchSize) {
        const tensor = await extractor(data.texts.slice(i, i + batchSize), { pooling: 'mean', normalize: true });
        vectors.push(...tensor.tolist());
        self.postMessage({ progress: { status: 'embedding', done: vectors.length, total: data.texts.length } });
      }
      self.postMessage({ id: data.id, vectors, embedMs: performance.now() - t, info });
    } catch (error) { self.postMessage({ id: data.id, error: error.message }); }
  });
};
