// Ensures pinned model weights and the production build exist; safe to re-run.
import { createHash } from 'node:crypto';
import { access, mkdir, readFile, rename, writeFile } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { dirname, join } from 'node:path';

const MODEL = 'Xenova/all-MiniLM-L6-v2';
const REVISION = '751bff37182d3f1213fa05d7196b954e230abad9';
// Pinned upstream digests; a local file is kept only when it matches.
const FILES = {
  'config.json': { sha256: '7135149f7cffa1a573466c6e4d8423ed73b62fd2332c575bf738a0d033f70df7' },
  'tokenizer.json': { sha256: 'da0e79933b9ed51798a3ae27893d3c5fa4a201126cef75586296df9b4d2c62a0' },
  'tokenizer_config.json': { sha256: '9261e7d79b44c8195c1cada2b453e55b00aeb81e907a6664974b4d7776172ab3' },
  'special_tokens_map.json': { sha256: 'b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3' },  'onnx/model.onnx': { sha256: '759c3cd2b7fe7e93933ad23c4c9181b7396442a2ed746ec7c1d46192c469c46e' },
  'onnx/model_fp16.onnx': { sha256: '2cdb5e58291813b6d6e248ed69010100246821a367fa17b1b81ae9483744533d' },
  'onnx/model_quantized.onnx': { sha256: 'afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1' },
};
const modelsDir = join('public', 'models', MODEL);

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

async function download(name, destination) {
  const { createWriteStream } = await import('node:fs');
  const { Readable } = await import('node:stream');
  const bases = [process.env.HF_ENDPOINT || 'https://huggingface.co', 'https://hf-mirror.com'];
  for (const [index, base] of bases.entries()) {
    for (let attempt = 1; attempt <= 2; attempt++) {
      const temporary = `${destination}.part`;
      try {
        const response = await fetch(`${base}/${MODEL}/resolve/${REVISION}/${name}`);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        await new Promise((resolve, reject) => {
          const stream = createWriteStream(temporary);
          Readable.fromWeb(response.body).pipe(stream).on('finish', resolve).on('error', reject);
          stream.on('error', reject);
        });
        await rename(temporary, destination);
        return;
      } catch (error) {
        const last = index === bases.length - 1 && attempt === 2;
        if (last) throw new Error(`下载 ${name} 失败：${error.message}`);
        console.error(`${base} 下载 ${name} 失败，重试：${error.message}`);
      }
    }
  }
}

async function prepareModels() {
  const manifest = { model: MODEL, revision: REVISION, files: [] };
  for (const [name, expected] of Object.entries(FILES)) {
    const path = join(modelsDir, name);
    let bytes = await exists(path) ? await readFile(path) : null;
    if (!bytes || createHash('sha256').update(bytes).digest('hex') !== expected.sha256) {
      await mkdir(dirname(path), { recursive: true });
      console.log(`下载 ${name} …`);
      await download(name, path);
      bytes = await readFile(path);
      const digest = createHash('sha256').update(bytes).digest('hex');
      if (digest !== expected.sha256) throw new Error(`${name} 校验失败：${digest}`);
    }
    manifest.files.push({ file: name, bytes: bytes.length, sha256: expected.sha256 });
  }
  await writeFile('public/model-manifest.json', JSON.stringify(manifest, null, 2) + '\n');
}

await prepareModels();
if (!await exists('dist/index.html')) {
  console.log('构建前端 …');
  const build = spawn('npm', ['run', 'build'], { stdio: 'inherit', shell: process.platform === 'win32' });
  if (await new Promise(resolve => build.on('close', code => resolve(code !== 0)))) throw new Error('构建失败');
} else console.log('dist 已存在，跳过构建。');
