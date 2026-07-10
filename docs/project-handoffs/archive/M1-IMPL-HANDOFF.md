# M1 实现阶段 —— 任务书（M1-IMPL-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-09，M1 契约任务收口（五契约冻结 + contracts 包 + mock + P1 形状验证）之后 |
| 用途 | **M1 实现阶段的唯一任务书入口**：把冻结契约变成能跑的产品。前置任务书 [M1-HANDOFF.md](M1-HANDOFF.md)（契约任务）已完成归档 |
| 上游事实源 | [engineering_docs/](../../../../engineering_docs/README.md) 五份契约（00 选型 + A–E）· 代码仓 [coagentia/](../../../README.md) · [SESSION-HANDOFF.md](SESSION-HANDOFF.md)（会话快照）· [CoAgentia-PRD.md](../../../../docx_agenthub/CoAgentia-PRD.md) §8 里程碑 |
| 执行计划 | [M1-DEV-PLAN.md](../../../../M1-DEV-PLAN.md)（2026-07-09）：模块步骤分解、阶段/会话切分、测试五层、验收映射、风险清单——**开发会话按它推进，进度状态也记在它的 §1** |
| 出口标准 | **PRD M1 出口：两个 Agent 在频道里完成一次真实对话与文件产出**（§8 逐条验收清单） |

---

## 0. 一句话目标

契约阶段回答了"形状是什么"，实现阶段回答"它真的跑起来"。两条线并行：**线 A 后端**（server + daemon + Claude Code 适配器）与**线 B 前端**（15 屏 React 重搭），各自消费现成的契约与 mock，最后在 §8 验收清单上汇合。

## 1. 现有资产盘点（拿来即用，勿重复建设）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| 五份契约 | [engineering_docs/](../../../../engineering_docs/README.md) | A v1.0.1（34 表）/ B v1.0.1（20 错误码、39 个 M1 端点）/ C v1.0（56 事件）/ D v1.0（29 帧）/ E v1.0——实现的唯一权威，实现偏离先改契约 |
| contracts 包 | `coagentia/packages/contracts` | Pydantic 唯一源：枚举/Row/Public/REST/WS/daemon 帧/常量/指纹内核。**server 与 daemon 直接 import，不得重复定义字面量**（契约 A §8.1） |
| manifest 对照测试 | `coagentia/packages/contracts/tests/` | 38 条测试把契约逐名钉死——改契约文档必须同步改 manifest，反之亦然 |
| fixtures | `coagentia/packages/fixtures/` | 设计稿同款 84 行（seed.json）+ WS 时间线 + 指纹金标判例；**可直接作为真 server 的种子数据与测试数据** |
| mock server | `coagentia/apps/mock-server` | `uv run coagentia-mock`（127.0.0.1:8642）：前端线的数据源，直到真 server 就绪 |
| 契约一致性测试 | `coagentia/apps/mock-server/tests/test_contract_conformance.py` | **这套测试就是真 server 的现成验收套件**——见 §5 纪律 3 |
| contracts-ts | `coagentia/packages/contracts-ts` | 生成物（`pnpm gen` 重跑 diff 为空守门）；前端唯一类型来源 |
| P1 验证屏 | `coagentia/apps/web` | React 19 + Vite 种子：api.ts/ws.ts/App.tsx 可演化为正式基座；截图实证在 `coagentia/docs/verify/` |

## 2. 两条线与汇合点

```
线 A（后端）: A1 建表 → A2 账本 → A3 REST → A4 WS ─┐
                          A5 daemon 网关 → A6 daemon → A7 适配器 ─┤→ A8 集成 → §8 出口验收
线 B（前端）: B1 基座 → B2 批次重搭 15 屏（吃 mock）──────────────┘（B 线切真端点）
```

- A3/A4 完成后，**web 从 mock 切真 server 应零改动**（同一契约形状）——这是同源承诺的最终验证点，也是两线的汇合仪式。
- 线 B 不等线 A：mock 已能驱动全部 M1 界面形状；线 A 不等线 B：验收可用 P1 种子屏 + curl。

## 3. 线 A：后端任务分解（模块 → DoD）

| # | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- |
| A1 | server 骨架 + M1 建表 | `apps/server` 按 00 §3 模块目录；SQLAlchemy 模型 import contracts 枚举；Alembic 首迁移 = 契约 A §5 **M1 批次 17 表**；PRAGMA（A §1）；不可变表触发器（messages/files/ledger/diagnostic/token_usage 禁 UPDATE/DELETE，RAISE 兜底） | `alembic upgrade head` 建库成功；**表结构对照测试**（列名/类型/约束 ↔ contracts manifest）；fixtures seed 可灌库 |
| A2 | ledger 模块（地基，先建） | A §4.7：`UNIQUE(op_id)` + request_hash 断言 + batch 维度 + 三条恢复规则 | 幂等测试三件套：同键同指纹跳过返回原结果 / 同键异指纹 fail-closed（批次置态 + 诊断 + 告警卡路径）/ 无 `:done` 从头重放补尾段（拆解设计 A5 的 DB 侧半边） |
| A3 | REST M1 端点 | B §4.1–4.6 全部 39 条 + 错误路径 + `Idempotency-Key` 复用账本（B §1）+ 文件 staging/GC（D §9.2）+ bootstrap（POST /workspace 自动建 Owner/#all/空画布） | **把 mock 的契约一致性测试参数化后跑在真 server 上全绿**（§5 纪律 3）；错误码/形状零偏差 |
| A4 | 浏览器 WS | C 全文：信封四要素、连接内 seq、应用层心跳、**事务后发射**（C §1.4）、断线重同步端点组（B §6）、订阅流（diagnostic.appended） | C 的行为测试：hello/ping-pong/seq 单调/写端点广播/重连后 REST 重建一致；contracts 的 Envelope 模型直接校验每帧 |
| A5 | daemon 网关 | D §2–§7：`/api/daemon/ws` Bearer 握手、单连接顶掉、心跳、**对账器**（D §4.4 八条规则，重连+60s 周期同一套）、指令/查询/上报 M1 子集、遥测落库（usage ULID 去重） | D §11 验收用例 2/3/4/5/6/8（M1 适用集）逐条自动化 |
| A6 | daemon 本体 | `apps/daemon`（uvx 入口）：保连接/重连退避、进程表、投递执行、遥测缓冲（`daemon/buffer/` JSONL）、数据目录（D §9.1） | 断连恢复测试：杀 daemon → 重连 → hello 对账 → Agent 自动 resume（status 列期望集合）；缓冲重传不虚增 |
| A7 | Claude Code 适配器 | E 全文：命令行拼装 + `CLAUDE_CONFIG_DIR` 隔离 + 凭证/技能物化、coagentia MCP 行为通道（M1 七工具 → REST）、生命周期映射、崩溃熔断、activity 相位聚合、usage 提取 | **E §10 八条冒烟用例对真 claude CLI 实测**（帧锚定测试落地，E §11.2/11.3 两个开放问题顺手关闭）；用例 2（一次完整对话）是 A 线的心脏验收 |
| A8 | M1 集成 | 端到端串联：Add Computer → daemon 接入 → 创建两个 Agent → #build 对话 → 文件产出 | §8 清单全绿 + 截图/录屏实证 |

**推进顺序**：A1→A2→A3→A4 严格串行（地基链）；A5 可在 A3 后启动；A6/A7 依赖 A5，与 A4 并行。A7 建议先做 E §10 用例 1–3 的最小闭环（启动/对话/重启），再补护栏与细节。

## 4. 线 B：前端任务分解（模块 → DoD）

| # | 模块 | 内容 | 完成判据（DoP） |
| --- | --- | --- | --- |
| B1 | 正式基座 | `apps/web` 升级：TanStack Router（**类型化 search params，深链 `?tab=&thread=&task=&node=` 是一等需求**，00 约束 6）+ TanStack Query + zustand；IBM Plex/Departure Mono **本地打包**、lucide-react（HANDOFF 复发点 5：实现期不走 CDN）；WS 重连 UI（顶部 2px warning 条 + toast，C §2） | 路由骨架跑通深链还原；P1 屏迁入基座后与验证屏截图一致 |
| B2 | 15 屏批次重搭 | 按设计线批次序消费归档稿：批 1（P1/P5）→ 批 2（P0a/b/c、P6、P7）→ 批 3（P3/P4/P8/P9/P10/P11）→ 批 6（P12/P13/P14/P15）；数据全部来自 mock/contracts-ts | **每屏 = 设计线 verify SOP**：playwright 1440×900 截图 ↔ 归档稿目检对照；复发性修正点 5 条（HANDOFF）逐屏自查；mock 缺数据的屏先登记契约再补 mock（§5 纪律 4） |
| B3 | 画布（P2 五态） | React Flow + dagre；M1 只需静态骨架（画布数据 M3 才有），批 4 屏可后排 | 骨架渲染 fixtures 画布行（空画布 + 基线徽章） |
| B4 | 切真端点 | mock → server 的 base URL 切换 + 逐屏回归 | 零类型改动、零形状适配代码——若需要适配层即契约违约，回头修 server |

## 5. 纪律（本轮立下的惯例，实现阶段继续有效）

1. **契约 ↔ manifest 双向同步**：新增端点/事件/表/错误码/帧 → 先改契约文档（升版本 + 变更记录）→ 改 contracts 包 → manifest 测试变红转绿。任何一环跳过都算违约。
2. **生成物只经脚本重生成**：fixtures（`gen_fixtures.py`）、golden（`gen_golden_fingerprint.py`）、contracts-ts（`pnpm gen`）——重跑 diff 必须为空；将来入 CI。
3. **契约一致性测试套件复用**：把 `test_contract_conformance.py` 的 app 对象参数化（pytest fixture 注入 mock app 或真 server app），一套测试两处跑——真 server 的 REST/WS 验收不用重写。
4. mock 是形状源不是逻辑源：给 B 线补屏数据时只加**读端点与 fixtures**，不在 mock 里实现业务（freshness/gating 等只活在真 server）。
5. 每完成一个模块：更新 [engineering_docs/README.md](../../../../engineering_docs/README.md) / [HANDOFF.md](HANDOFF.md) 工程线行 / [SESSION-HANDOFF.md](SESSION-HANDOFF.md)（惯例已立）；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问（推荐项放第一）；已拍板勿再问。

## 6. 已拍板勿再问（速查，溯源见各契约与 SESSION-HANDOFF §4）

Python 后端/daemon + TS 前端 + Pydantic-first 单向生成 · 无指令 outbox（DB 事实源对账补发，D）· 无浏览器 WS outbox（REST 重同步，C）· agents.status 列不被级联改写（D §2）· deliver ack 写 read_positions = Agent 已读游标（D §8.3）· Agent 行为唯一出口 = coagentia MCP，正文不外发（E）· activity 相位聚合不加工具级事件（E §7.3，关了 C §10.1）· 文件预上传走 staging + 24h GC（D §9.2）· bypassPermissions + api-key 明文 = owner 决策 · 单人 MVP 无登录 · 消息不可变 · DM 不承载任务 · force-start 仅人类。

## 7. 挂账开放问题总表（各契约"不阻塞冻结"项的归属与时机——勿当漏项重新发明）

| 出处 | 问题 | 归属时机 |
| --- | --- | --- |
| E §11.2 / §11.3 | 辅助旗标终表（--verbose 等）· busy 期 stdin 的确切 CLI 行为 | **A7 冒烟测试落地时顺手关闭**（本阶段唯一必须收的两条） |
| E §11.4 | DISALLOWED_TOOLS 终表 | A7 实测定 |
| E §11.1 | deliver ack 后崩溃的消息小窗口 | 实测成为痛点再议 |
| D §12.4 | 遥测缓冲上限默认值 | A6 实测调整（非协议变更） |
| D §12.1 | projects 缺 computer_id | **M6 建表前修契约 A**（已登记） |
| D §12.2 | daemon 打包（uv build/PyInstaller） | MVP 用 uvx，后议 |
| A §10.4 / B §8.2 | FTS5 中文分词与搜索质量 | M2 实测 |
| A §10.3 · B §8.3 · C §10.2/10.3 · D §12.3 | api-key 轮换 · Agent 频控 · 多人事件过滤 · WS 压缩 · 跨机预览代理 | 多人化/云化后置 |
| 00 §7.3 | TanStack Router 降级条款 | B1 期间任一深链场景受阻超一天 → React Router v7 |

## 8. M1 出口验收清单（全绿即里程碑收口）

> **2026-07-09 收口**：出口 12 条全部达成（工作流 A1–A8 + B1–B2a + 真机 A8/B4 实证）。分支 `m1-impl`，8 commits。
- [x] `alembic upgrade head` 建 M1 批次 17 表，PRAGMA 与不可变触发器就位（A1）—— 实证：fresh 库 17 表 + 10 触发器
- [x] 契约一致性测试套件在**真 server** 上全绿（A3/A4，与 mock 同套）—— test_conformance_dual 双跑
- [x] 账本幂等三件套测试过（A2）
- [x] `uvx coagentia-daemon …` 一行接入 → Connected（A5/A6）—— 真 daemon 握手：computer status=connected + detected_runtimes 真探测
- [x] 创建 Agent → starting→idle presence 点实时（A7 用例 1）—— 真 spawn→idle
- [x] **两个 Agent 在频道完成真实对话**（A8）—— 真机实证：Owner @Pat → [Pat]「I'm Pat…@Hank?」→ [Hank]「Hey @Pat!」（作者归属正确、@触发级联唤醒）
- [x] **文件产出**（A8）—— 真机实证：Agent 写 hello.txt → upload_file → send_message 绑定；server files/ 存「CoAgentia M1 works」
- [x] kill Agent → 自动拉起 --resume；三档重置（E 用例 3/4）—— A7 冒烟用例 3 独立复跑绿（跨重启记得 codeword）；三档重置 + 崩溃熔断单测覆盖（live 崩溃测因杀到孤儿进程不确定，逻辑单测已覆盖）
- [x] usage/诊断落库，重传不虚增（ULID 去重）—— 真机实证：token_usage 9 行 id 唯一、diagnostic 50 行五类
- [x] daemon 断连 → 级联 Offline → 重连自动 resume（D 用例 3/8）—— 假 daemon 桩测 D§11 用例 3/8 + server 重启 live 重连 resume（两 Agent 回 idle）
- [x] **P1 屏从 mock 切真 server 零改动**（B4）—— 真机实证：仅改 API_BASE 一常量，P1 对真 server 渲染 #build/消息/presence/WS 一致（task chips 缺席 = M2 端点边界，前端优雅降级），零 CORS 零 shape 适配（docs/verify/b4-p1-real-server.png）
- [x] 全程截图/录屏实证归档 `coagentia/docs/verify/`（backend curl/ws 输出 + 前端 b1/b2a/b4 截图）

## 9. 第一步建议

从 **A1+A2**（建表 + 账本）开工：它是一切的地基且完全无外部依赖；同一会话可顺手把契约一致性测试参数化（纪律 3），为 A3 铺路。线 B 若并行启动，先做 B1 基座（路由 + 字体本地化），P1 迁移即验收。

## 附：本轮踩过的坑（环境备忘，勿重踩）

1. **裸 uvicorn 无 WS 协议库**：不装 `websockets` 时对升级请求直接 404——server 依赖里必须带（mock 已修，apps/server 记得）。
2. **Vite 默认绑 IPv6 `[::1]`**：win32 上 curl/部分工具连 127.0.0.1 会拒绝——dev 一律 `--host 127.0.0.1`。
3. **FastAPI TestClient 的孤儿任务**：非 `with client:` 用法下每请求一个 portal，handler 里 `asyncio.create_task` 的后台任务随请求结束被丢——需要跨请求存活的后台行为要么内联 await，要么用 lifespan 模式的持久 portal。
4. Crockford ULID 字母表**排除 I/L/O/U**——手造测试 id 时别用这些字母。
5. Pydantic 带默认值的字段在 JSON Schema 中非 required → 生成 TS 是可选属性——前端消费时用局部常量收窄，别与生成器搏斗。
> 归档说明：本文是 M1 实现阶段的已完成任务书，内容不再更新。阶段结论见 [项目记录](../PROJECT-RECORD.md)，当前状态见 [当前交接](../CURRENT-HANDOFF.md)。
