# @coagentia/daemon-ts

CoAgentia daemon 的 TS 对等实现（TS 迁移批 daemon 先行，任务书 = `docs/project-handoffs/TS-MIGRATION-HANDOFF.md`）。
对等基准 = `apps/daemon`（Python）；契约 D v1.0.5 零修订，逐义务对等迁移，**零行为改进**。

## 运行

- **node ≥22.18 直跑 TS**（type stripping）：`node apps/daemon-ts/src/cli.ts --server-url ... --api-key ...`。
- 零构建步、零运行时 npm 依赖（WS=原生 WebSocket，子进程/fs/crypto=node 内置）。

## 体例（波次代理必读；违反=返工）

1. **erasable syntax only**：禁 `enum`/`namespace`/参数属性/`export =`——用 `const` 对象 + 字面量联合类型（contracts-ts 同风格）。
2. **包内导入显式 `.ts` 扩展名**（node 直跑硬要求）：`import { newUlid } from './util.ts'`。
3. **对 `@coagentia/contracts-ts` 只许 `import type`**（tsconfig `verbatimModuleSyntax` + 测试锚双守门）：运行时常量一律来自 `src/generated/constants.ts`（pnpm gen 产物，禁手改）。
4. 模块文件名与 py 源一一对应（`buffer.py → buffer.ts`）；导出符号 camelCase（`new_ulid → newUlid`）；类名不变。
5. 每个 py 测试文件对应同名 `tests/<name>.test.ts`，py 用例逐条对应（用例名保留语义）；行为差异必须在测试注释登记，不许静默"改进"。
6. win32 校准条款（任务书 §4）优先于 py 源的直译：子进程编码/杀树/大帧/管道收尾按条款写。
7. 子进程测试收尾必杀（taskkill /F /T），探针/测试不得自带掩盖性 env（PYTHONIOENCODING 教训）。
