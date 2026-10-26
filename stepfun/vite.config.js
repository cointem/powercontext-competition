import { defineConfig } from 'vite';
export default defineConfig({
  server: { host: '127.0.0.1', port: 15173, strictPort: true, proxy: { '/api': 'http://127.0.0.1:15174' } },
  worker: { format: 'es' },
  optimizeDeps: { exclude: ['@huggingface/transformers'] },
});
