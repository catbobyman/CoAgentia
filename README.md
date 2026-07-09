# CoAgentia monorepo

契约驱动、流程可编排、护栏可干预的多 Agent 协作平台。**契约事实源 = `../engineering_docs/` 的五份 M1 契约（A–E）**；本仓库代码不得偏离契约文档，新增端点/事件/表/帧先登记进对应契约。

## 布局（对应 00-技术选型.md §3）

| 路径 | 内容 |
| --- | --- |
| `packages/contracts` | 【唯一源，Python】Pydantic v2 模型：实体（契约 A）/ REST（契约 B）/ WS 事件（契约 C）/ daemon 帧（契约 D）/ 常量目录（契约 E）+ `kernel/`（指纹，规范 = 契约 A §2） |
| `packages/contracts-ts` | 【生成物】TS 类型：`pnpm gen` 重新生成，`src/generated/` 禁止手改 |
| `packages/fixtures` | 设计稿同款样例数据（Memcyo/Pat/Hank/Rin、#build 番茄钟、任务 #1–#7）+ `golden/` 跨语言金标判例 |
| `apps/mock-server` | 契约驱动 mock：fixtures over REST + WS 事件推送；`uv run coagentia-mock` |
| `apps/web` | P1 会话屏形状验证（M1）；15 屏重搭为独立工作流 |
| `apps/server` / `apps/daemon` | M1 实现阶段启动（目录预留，见各自 README） |

## Verify SOP（全链路）

```
uv sync && uv run pytest          # 契约对照测试 + fixtures 校验 + mock 契约一致性
pnpm install && pnpm gen          # 导出 schema/openapi → 生成 TS（生成后 git diff 应为空）
pnpm typecheck
uv run coagentia-mock             # http://127.0.0.1:8642
```
