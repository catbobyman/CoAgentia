// vitest 全局测试初始化(仅本文件依赖 test.globals:true——@testing-library/react 靠全局 afterEach
// 自动 cleanup;其余测试文件继续显式 `import { describe, it, expect } from 'vitest'` 的既有写法)。
import '@testing-library/jest-dom/vitest';
