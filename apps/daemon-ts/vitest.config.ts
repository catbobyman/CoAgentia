import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
    // 子进程/真 git 类测试在 win32 偏慢，单测超时放宽到 30s（对齐 py 套 pytest 默认无超时的实况）
    testTimeout: 30_000,
  },
});
