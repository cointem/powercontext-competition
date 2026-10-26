// Control-flow regression; real model inference is verified in the browser.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('./src/embedding.worker.js', import.meta.url), 'utf8'))
  .replace("import { pipeline, env } from '@huggingface/transformers';", '');

for (const gpu of [true, false]) {
  test(`ordinary import and query complete with default batching, GPU ${gpu}`, async () => {
    let respond;
    const context = vm.createContext({
      env: { backends: { onnx: { wasm: {} } } }, performance,
      navigator: gpu ? { gpu: { requestAdapter: async () => ({ features: new Set(['shader-f16']) }) } } : {},
      pipeline: async () => async texts => {
        assert.ok(texts.length > 0, 'inference must receive nonempty input');
        return { tolist: () => texts.map(text => [Number(text)]) };
      },
      self: { postMessage: message => { if (message.id) respond(message); } },
    });
    vm.runInContext(source, context);
    for (const [id, texts, batchSize] of [
      [1, Array.from({ length: 19 }, (_, i) => String(i)), null],
      [2, ['42'], null],
      [3, ['3', '4', '5'], 2],
    ]) {
      const result = await new Promise(resolve => {
        respond = resolve;
        context.self.onmessage({ data: { id, texts, batchSize, options: { backend: 'auto', dtype: 'fp16' } } });
      });
      assert.equal(result.error, undefined);
      assert.equal(result.info.backend, gpu ? 'webgpu' : 'wasm');
      assert.deepEqual(Array.from(result.vectors, vector => Array.from(vector)), texts.map(text => [Number(text)]));
    }
  });
}
