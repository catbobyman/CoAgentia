# TS 迁移批（daemon 先行）—— 任务书（TS-MIGRATION-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 文档类型 | 专项批任务书 + 执行计划合一（DEDAG-HANDOFF 体例） |
| 版本/日期 | v1.0 / 2026-07-19（立项） |
| 收口状态 | **进行中** |
| 分支 | `agent/b1-delivery-gating-deadlock-fix`（工作分支，main 随批快进） |
| 上游决策 | DEDAG-HANDOFF §1 裁决 #5（owner 2026-07-18）：「本批语言现栈(Python)做；纯 TS 迁移另立项(**终态架构已定 TS，daemon 先行**)」；CURRENT-HANDOFF 标题「接续 = TS 迁移批未立项」；owner 2026-07-19 指示「开始TS迁移，使用多agent和workflow来执行」 |
| 摸底证据 | 本会话五路摸底（daemon 架构/契约 D 义务/fingerprint 权威/TS 工具链/决策锚点）+ 六路 win32 node 校准探针全 PASS（脚本 = `scratchpad/tscal/`，条款 = §4） |
| 产品语义 | **零产品行为变化**：TS daemon 是 py daemon 的逐义务对等替身（契约 D v1.0.5 零修订、帧形零变化），server/web/contracts(py 权威) 零改动 |

## §0 一句话目标

新建 `apps/daemon-ts`（node ≥22 直跑 TS，零新运行时依赖），把 `apps/daemon` 全部 17 指令/5 查询/11 上报义务面 + 双 CLI 适配器 + MCP stdio 桥逐条对等迁移并通过全量对等测试与实机 verify；py daemon 保留并存，退役另批拍板。

## §1 裁决表（本批拍板；owner 可否决，否决处按纪律回改）

| # | 裁决点 | 拍板 | 依据 |
| --- | --- | --- | --- |
| 1 | 范围 | **daemon 先行**：新建 `apps/daemon-ts` 全功能对等；py daemon 保留并存（默认启动仍 py daemon），退役时机 = TS daemon 实机 verify 全绿后 owner 另批拍板 | DEDAG #5 原文「daemon 先行」；并存期两 daemon 不同机可混跑（协议同版） |
| 2 | 冻结七表 drop | **本批不动**（登记接续=server TS 化批清算） | 0001 `metadata.create_all` 依赖 live model，drop 是独立迁移手术，与 daemon 迁移零耦合 |
| 3 | fingerprint 权威归属 | **本批不动**（py 权威 `packages/contracts/kernel/fingerprint.py` 照旧） | 实证：daemon 对 kernel 零 import（全目录 grep 零命中）；全部活消费点在 server 幂等面 |
| 4 | TS 运行方式 | **node ≥22 直跑 `.ts`**（type stripping，本机 v22.22.1 实测通过）：零构建步、零 tsx/ts-node；约束=erasable syntax only（**禁 enum/namespace/参数属性**，用 const 对象+字面量联合，与 contracts-ts 风格一致）；daemon-ts package.json 写 `engines.node >= 22.18` | 实测 `node xxx.ts` exit 0；契约 tsconfig 全仓 noEmit 惯例不破 |
| 5 | 运行时依赖 | **零新 npm 运行时依赖**：WS=原生 `WebSocket`（undici）；子进程/fs/crypto 全 node 内置。devDeps 仅 typescript/vitest/@types/node | cal4 实证：契约 D 心跳=应用层帧（Transport 抽象仅 send/recv/close），原生无 ping API 无碍；5MB 帧/close code 4000+/1006 感知全过 |
| 6 | contracts-ts 消费纪律 | daemon-ts 对 `@coagentia/contracts-ts` **只许 `import type`**（剥离期整句擦除，运行时零解析）；运行时常量经 **pnpm gen 扩一个输出目标** `apps/daemon-ts/src/generated/constants.ts`（事实源=coagentia_contracts constants.py+daemon.py，export_schemas.py+gen.mjs 各扩一段；gen 后 diff 空守门同款、禁手改）；帧窄化层 `protocol.ts` 手写（InstrType→payload 映射+接收边界校验） | 实测：contracts-ts 无扩展名相对导入过不了 node 原生 ESM；pnpm 符号链接 realpath 穿透 node_modules 限制成立；type-only import 擦除实测通过 |
| 7 | 包内导入体例 | 显式 `.ts` 扩展名（node 直跑硬要求）+ tsconfig `allowImportingTsExtensions: true`；vitest W0 首件即验此链路 | 实测 `import {x} from "./dep.ts"` node 直跑通过 |
| 8 | ULID/时间戳同构 | `util.ts` 与 py `util.py` 同算法（Crockford Base32 26 位/ISO-8601 毫秒 Z）；py 生成固定输入 golden 判例入 `apps/daemon-ts/tests/fixtures/`，vitest 逐字节守门 | 字典序一致性是 exactly-once 去重根基 |
| 9 | 测试策略 | py daemon 全部测试**对等移植** vitest（node 环境，无 happy-dom）；测试底座 TS 版（RecordingTransport/AutoAckTransport/FakeProc/SpawnRecorder/帧构造器）；真 server 集成测归实机 verify 探针（python harness 起隔离端口 uvicorn × node daemon）不进 vitest | daemon py 基线 214 passed / 4 skipped；守门新增独立行「daemon-ts vitest N」只增不减 |
| 10 | MCP 宿主接缝 | daemon-ts 的 cmdline/codex_cmdline 物化 mcp_command 指向 **node 版 MCP 入口**（daemon-ts 自身 `mcp` 子命令）；py daemon 的 `sys.executable -m coagentia_daemon mcp` 不动 | 适配器拉起的 MCP server 身份随宿主语言 |
| 11 | 契约面 | **A–E 全零修订**（帧形/端点/工具零变化）；`engineering_docs/00-技术选型.md` 升版补记终态 TS 决策（DEDAG #5）+ 本批 daemon 先行（纪律 1 文档对齐） | 摸底证实 00-技术选型 尚无终态 TS 表述 |
| 12 | 命名/杂项 | 包名 `@coagentia/daemon-ts`，目录 `apps/daemon-ts`；base.py 与 adapter.py 同名 RuntimeAdapter 消歧**不做**（原样直译，挂账不顺手修）；logconfig 用手写滚动文件写入器（8MB×3 对齐），不引 pino/winston | 纪律 7 |

## §2 义务面与非目标

- **义务面事实源**：契约 D v1.0.5（`engineering_docs/04-daemon-server协议.md`）+ 契约 E/E2 适配器接缝；对等基准 = `apps/daemon` 现实现。17 指令 / 5 查询 / 11 上报 / hello-boot_nonce-previews 快照 / ack 三态+自然键幂等+短窗 2048（ack 写入传输后才记忆）/ worktree 后台单车道 status→ack 保序 / 缓冲五轨 JSONL 原子重写+flush 保序（deploy.log 先于 finished）/ 心跳应用层 ping-pong（heartbeat_sec 下发，10s pong 超时断连）/ 指数退避 1→30s 无限重连 / 断连不杀 Agent 与预览进程 / 崩溃熔断 1s/5s/15s+5min 窗 3 次放弃 / OAuth 失败重投一次 / 部署终态不重跑（铁律 3）。逐条验收清单以摸底产出为准（本批执行时由各波 DoD 引用）。
- **非目标**：server/web/contracts(py) 任何改动；冻结表 drop（裁决 #2）；行为改进/重构/改名（含 base.py 消歧、buffer 追加制改造——语义原样直译）；py daemon 退役；多机多租户预留位。

## §3 波次（Workflow 编排；波间 vitest+typecheck 绿才进下波；文件域不相交可并行）

| 波 | 内容 | 依赖 |
| --- | --- | --- |
| **TS-W0 底座**（主循环亲做） | 工作区接入（pnpm-workspace.yaml +1 行/package.json/tsconfig/vitest.config）+ gen 扩常量产物（export_schemas.py+gen.mjs）+ `util.ts`（golden）+ `paths.ts` + `logconfig.ts` + `transport.ts`（原生 WS 包装+TransportClosed）+ `protocol.ts` 窄化 + 测试底座（RecordingTransport/AutoAckTransport/帧构造器）+ 首条 vitest 冒烟（验 .ts 扩展名链路） | 无 |
| **TS-W1 纯函数与落盘**（并行×4） | `buffer.ts`（五轨 JSONL/原子重写/环形上限）∥ `adapters/encoding.ts`+`cmdline.ts`+`codex_cmdline.ts`（纯函数+物化）∥ `adapters/frames.ts`（FrameRouter 桩帧全量测试翻译）∥ `adapter.ts`（Sink/RuntimeAdapter 接口+FakeAdapter） | W0 |
| **TS-W2 子进程底座**（checks 先行，余并行×4） | `checks.ts`（run_process/杀树单点）→ `git.ts`（run_git/worktree 状态机/diff 三命令 NUL 解析/取消恢复）∥ `preview.ts`（端口注册表/健康竞速/stopping 判序）∥ `deploy.ts`（流式批量/chunk_seq/不重跑）∥ `probe.ts` | W0（校准条款 §4 回填） |
| **TS-W3 适配器与 MCP**（并行×3） | `adapters/claude_code.ts`（Process+RuntimeManager：熔断/三档重置/32MB 行上限/stderr 排空）∥ `adapters/codex.ts`（握手/审批表/turn 队列/_pending 清账）∥ `adapters/mcp.ts`（stdio JSON-RPC 循环，parse error 必回铁律） | W1、W2 |
| **TS-W4 集成收口**（主循环亲做/单强代理） | `handlers.ts` → `client.ts`（reader/heartbeat/flush 三并发、worktree 后台通道、per-frame ack Future、断连清账）→ `cli.ts` 入口 | W1–W3 |
| **TS-W5 守门+实机** | 六门全绿（pytest 不回归 977/4、web vitest 266、**daemon-ts vitest 新行**、typecheck 三 tsc+pyright 0、ruff 净、gen 确定、build 绿）+ 实机 verify：隔离端口真 uvicorn × node daemon 全链探针（握手/对账/deliver/worktree/check/preview/deploy）+ 真 CLI 冒烟；证据归档 `docs/verify/` | W4 |

## §4 win32 node 校准条款（六探针全 PASS，脚本=`scratchpad/tscal/`；写代码前先读）

1. **子进程编码**（cal1）：spawn python 子进程必注 `PYTHONIOENCODING=utf-8`（stdin 缺失=exit 0 静默 mojibake，比 stdout 崩溃更隐蔽）；git 输出默认 UTF-8 完好；node 读侧**严禁逐 chunk `.toString('utf8')`**（4 字节 emoji 跨 64KB chunk 实测碎成 U+FFFD）——用 `setEncoding('utf8')` 或整行 Buffer.concat 后一次解码。
2. **大帧行读**（cal2）：B-4 的 64KB 上限在 node 无对应物；标准读法=手写 Buffer 累积按 `\n` 字节切分（32MB 帧 47ms，比 readline 快 2.4×）；**必须自设 32MB 行上限**（node 累积无界，超限=丢帧上报协议错误不崩读循环）；内存预算=最大帧 3~4 倍。
3. **杀树**（cal3）：唯一写法 `spawn('taskkill',['/F','/T','/PID',pid])`，code 0=杀净（3 层 ≤1s）、**code 128=已死视为幂等成功**；`child.kill()` 杀不到孙禁用于树；`.cmd` 拉起必须 `shell:true`（node 22 裸 spawn EINVAL）且杀树以壳 pid 为根；taskkill/tasklist 输出是 GBK——只判退出码与 ASCII 字段；存活探测轻量档 `process.kill(pid,0)`。
4. **WS**（cal4）：原生 WebSocket 够用；连上即设 `binaryType='arraybuffer'`；重连唯一触发=close 事件（对端强杀=1006、<0.5s 感知，**不要依赖 error 先行**）；send 同步入队保序；背压看 `bufferedAmount`。
5. **端口自持**（cal5）：`net.createServer().listen({port, host:'127.0.0.1', exclusive:true})`，EADDRINUSE 走 **error 事件异步判**（非同步 throw）；**严禁默认 host**（绑 '::' 时跨 runtime 互斥为零）；`reusePort` win32 ENOTSUP 禁用；taskkill 后端口即时可重绑（≤29ms），重启抢锁 ≤100ms 重试兜底即可。
6. **长驻管道**（cal6）：spawn 当拍**同步挂 stdout/stderr 消费者再 await**（否则子进程死亡残留数据静默丢，实测 0/32837 字节）；stderr 永不裸放（~128KB 即全链死锁，实测 8s 仅收 2/33 行）；stdin 背压 `write()===false → await once('drain')`（零时间代价）；**生命周期定稿只挂 'close'**（end/exit 顺序不定）；行 splitter 容忍 CRLF（win32 文本模式 +1 字节 `\r`）。
7. **（主循环亲测）运行时链路**：node 22.22.1 直跑 .ts 通过；pnpm workspace 符号链接 realpath 穿透 node_modules 类型剥离限制；contracts-ts 无扩展名导入运行时不可用→裁决 #6/#7 的 import 纪律由测试锚守门（grep src 禁止对 contracts-ts 的值导入）。
8. **WS 鉴权头**（cal7，主循环亲测）：标准 WebSocket API 无自定义头，但 node 内建（undici）扩展 `new WebSocket(url, { headers: {...} })` 实测 `Authorization: Bearer` 完好到达 python websockets 服务端——契约 D §2 Bearer 握手在原生 WebSocket 下成立（脚本 `scratchpad/tscal/cal7_*`）。

## §5 风险与对策

| 风险 | 对策 |
| --- | --- |
| vitest 对 `.ts` 扩展名导入/node 环境的兼容 | W0 首件冒烟即验；不过则回退包内无扩展名+仅 CLI 入口用启动壳（裁决 #7 降档，留痕） |
| client.ts 三并发任务语义翻译走样（reader 终结取消其余/Future 清账/Event 时序） | W4 主循环亲做；py 测试 test_reconnect/test_idempotency 全量对等移植先行红→绿 |
| 并行代理交叉改共享文件 | W0 先立全部共享底座；后续波代理只新增自有模块+测试文件，禁碰 package.json/tsconfig/他人模块 |
| 「模拟件掩盖面」家族 | 实机 verify 必真组件（真 uvicorn/真 git/真 CLI），探针不带掩盖性 env（cal1 规则 6） |
| 大批量迁移语义漂移 | 逐模块 DoD=「py 测试逐条对应 TS 测试」；行为差异必须登记（不许静默"改进"） |

## §6 挂账登记（本批新增，勿顺手修）

- **TS-①** `apps/web/src/lib/fingerprint.ts` DEDAG 后零消费零测试孤儿（双跑守门 TS 侧失守）；处置（补 golden vitest 或退役）等 owner 拍板。
- **TS-②** `scripts/gen_fixtures.py` 滞后 DEDAG：仍构建 canvases/EMPTY_BASELINE，重跑会把 canvases 写回 seed.json（不在 pnpm gen 守门链故未暴露）。
- **TS-③** base.py/adapter.py 同名 RuntimeAdapter 消歧（原样直译带入 TS，见裁决 #12）。

## §7 执行记录（v1.1，2026-07-19 收口）

- **执行模式**：Fable 单窗主编排 + 三个 Workflow（五路摸底 ∥ 六路 win32 校准 → 9 模块 W1∥W2 波 → 3 模块 W3 波）+ 3 收尾子代理（W4 client 测试移植 / todo 清账 / 真 CLI 冒烟诊断），当日立项当日收口。W4 核心（client.ts/handlers.ts/cli.ts/protocol/transport/底座）主循环亲做。
- **交付**：`apps/daemon-ts` 全模块（src 19 文件 + tests 23 文件）；vitest **247 passed / 4 skipped**（py 基线 214/4 逐用例对应+拆分增补，各文件头登记）；六门全绿（pytest 977/4 零回归）。
- **实机**：`scratchpad/tsdaemon_verify.py` **12/12 ALL PASS** + 真 claude CLI 冒烟 **3/3**（COAGENTIA_SMOKE=1）。证据 = docs/verify/TSDAEMON-VERIFY-EVIDENCE.md。
- **实机抓真缺陷**：构造器参数属性 6 文件 12 处——tsc/vitest 全放过、node strip-only 直跑即崩（「模拟件掩盖面」家族新例；全库批改 + `scratchpad/tscal/check_erasable.mjs` 常驻检出 + mcp 真 cli spawn 测试例守门）。探针自身教训：`uv run` 包装进程 terminate 杀不到 uvicorn 孙（cal3 孤儿形态实锤，收尾改杀树）；server 在线枚举值=`connected`、部署终态=`success`（谓词勿凭直觉）。
- **真 CLI 冒烟首跑 2 例超时根因 = 环境**：机器级 OAuth 凭证被 agent 侧轮换腐坏（py 同挂）；已手工修复并登记挂账 TS-④/⑤。
- **新挂账**：TS-①~⑤ 登记 CURRENT-HANDOFF §5。**接续**：py daemon 退役时机（owner 拍板）→ server/web TS 化另立项（冻结七表 drop 归彼批清算）。
