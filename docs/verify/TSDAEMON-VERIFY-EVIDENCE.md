# TS daemon 实机验证实证（TS 迁移批 daemon 先行）

日期：2026-07-19 · 执行：Fable（Claude Code 会话，owner 指示「开始TS迁移，使用多agent和workflow来执行」）
任务书 = `docs/project-handoffs/TS-MIGRATION-HANDOFF.md`；结果 JSON = `TSDAEMON-VERIFY-results.json`。

## 0. 环境

- 真 uvicorn（隔离端口 8931，临时库 alembic head + m6a_harness 种子）；**未触碰 8787 实机环境**。
- 真 node daemon：`node apps/daemon-ts/src/cli.ts --server-url ... --api-key ... --data-root <tmp>`
  （node v22.22.1 type stripping 直跑，零构建零运行时依赖；探针不带任何掩盖性 env）。
- 真 git（临时 scratch 仓库）+ 真部署子进程（node deploy.mjs）。
- 探针 = `scratchpad/tsdaemon_verify.py`（可复跑，`--keep` 保活）。

## 1. 协议链探针 12/12 ALL PASS

| # | 面 | 实证 |
|---|---|---|
| T0 | hello 握手 | node daemon 连上 → hello_ack ok → computer status=connected |
| T1 | query 代理 fs.tree | win32 盘符根视图 3 条经 daemon 回流 REST |
| T2 | worktree.ensure | writes_code 任务 → 真 git worktree 落盘（任务驱动派生） |
| T3 | worktree.merge | 202 → merged + merge_commit + main 含任务内容 + `--no-ff` merge commit |
| T4 | merge 幂等 | 已 merged 再触发 → 202 status=merged |
| T5 | 真冲突链 | 同文件冲突 → conflicted + `git merge --abort` 主干净 + 冲突任务自动派回（writes_code） |
| T6 | deploy 全链 | 201 受理 → deploy.run instr → deploy.log 流式 → deploy.finished → DB status=success + URL `https://tsdemo.example.com/app` 提取（缓冲重传 wire 面全通） |
| T7 | 断连语义 | taskkill daemon 树 → computer offline → merge 503 DAEMON_OFFLINE |
| T8 | 重启重连 | 新 daemon 进程（新 boot_nonce）→ 重连 connected（真重启对账面） |

## 2. 实机 verify 抓到并已修的缺陷（「模拟件掩盖面」家族新例）

- **不可剥离 TS 语法**：6 个 src 文件 12 处构造器参数属性（README 体例 1 违规）——tsc 合法、
  vitest 转译放过，唯 node strip-only 直跑 `ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX` 崩溃。
  全库扫同族批改为显式类字段（`scratchpad/tscal/check_erasable.mjs` 逐文件真 import 检出，
  终态 ALL-ERASABLE）；mcp 真 cli spawn 测试例已把 strip-only 真拉起路径纳入常驻守门。
- **探针自身两处谓词错**（非 daemon 缺陷，登记供后人）：computer 在线值 = `connected` 非
  `online`；部署终态值 = `success` 非 `succeeded`。
- **探针收尾孤儿**：`uv run uvicorn` 包装进程 terminate 杀不到 uvicorn 孙（cal3 孤儿逃逸形态
  实锤）→ 收尾改 `taskkill /F /T`。

## 3. 观察项（非本批回归，登记不顺手修）

- daemon 重启后 server 对账 #3 重放 worktree.ensure 于冲突任务 alias 行 → daemon 按
  WorktreeSafetyError「分支已在另一 worktree 使用」回 ack failed（py daemon 同场景同抛——
  ensure 以自身 task_id 算路径、分支已被原任务树占用）。属既有 server 侧对账语义面，与 TS 迁移无关。

## 4. 真 CLI 冒烟（COAGENTIA_SMOKE=1 门控）——3/3 ALL PASS（22s）

真 claude.exe 2.x × TS 适配器全链：`starting→idle→busy→[Thinking/Replying activity]→usage
（恰一条，ULID）→idle`，帧序与 py 实录一致；restart `--resume` 保上下文、MCP roundtrip 同过。

**首跑 2 例超时的根因 = 环境非代码**（诊断探针 `scratchpad/tscal/cal8_claude_smoke.mjs`，
现场归档 `cal8_out/`）：机器级 `~/.claude/.credentials.json` 的 refresh token 已被 agent 侧
自刷新轮换作废（活凭证在 `~/.coagentia/agents/<id>/` 隔离目录）；claude stream-json 模式不自
刷新，吐契约外 `control_request{oauth_token_refresh}` 后 401。**py daemon 同场景同挂**（当年
冒烟过是因 token 新鲜）。修复 = 把最新 agent 凭证复制回机器级 + 一次 `claude -p` 触发自刷新。
逐项 diff 证实 argv/凭证评分/CONFIG_DIR 隔离/stdin 编码/spawn 形态/stderr 排空/行读 TS 与 py
语义等价——凭证修复后同一套 src 一次通过。

**新挂账两条（登记 CURRENT-HANDOFF §5）**：① `oauth_token_refresh` 契约外帧 py/TS 均只防腐
计数不应答（两侧同族缺口，应答或可免手工修凭证）；② `materializeCredentials` 仅 agent←machine
单向同步，机器级凭证会因 agent 侧轮换腐坏（本次实锤），再腐坏时全新环境冒烟/新 Agent 必 401。

## 5. 单测对等与守门

- daemon-ts vitest：**247 passed / 4 skipped / 0 todo / 0 failed**（23 文件；4 skipped =
  平台门控 posix 例 + COAGENTIA_SMOKE 门）。py 基线 214 passed / 4 skipped 逐用例对应
  （拆分/增补见各测试文件头注释登记）。
- 六门：pytest **977/4**（零回归）· web vitest **266** · typecheck 三 tsc + pyright 全 0 ·
  ruff 净 · `pnpm gen` 确定（含新增 daemon-ts 常量产物）· web build 绿。
