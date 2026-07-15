import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8787',
        ws: true,
      },
    },
  },
  test: {
    // 组件行为测试(M3 P5 契约卡)需要真 DOM:happy-dom 比 jsdom 轻,足够渲染/断言用。
    environment: 'happy-dom',
    // globals:true 让 @testing-library/react 的 afterEach 自动 cleanup 生效;各测试文件仍可显式 import。
    globals: true,
    setupFiles: ['./src/test/setupTests.ts'],
  },
});
