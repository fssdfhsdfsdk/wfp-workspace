import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// 产物直接写入 Python 包内，随 wheel 离线分发；运行时不请求任何 CDN（DR-07）。
export default defineConfig({
  plugins: [react()],
  base: '/',
  build: {
    outDir: '../wfp/web/dist',
    emptyOutDir: true,
    assetsDir: 'assets',
    chunkSizeWarningLimit: 8000,
  },
  worker: { format: 'es' },
});
