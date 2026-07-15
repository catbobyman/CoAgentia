# M8-DEV-PLAN —— 逐模块执行计划与进度表

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-14，随 [M8-HANDOFF.md](M8-HANDOFF.md) 立项建立（任务书为范围与 DoD 权威，本文只管执行编排与进度） |
| 协作模式 | [COLLAB-MODEL.md](COLLAB-MODEL.md) v2「Fable 单窗编排」续用：波内并行派子代理执行/评审；**Fable 亲做关口** = L4a 读循环并发改造、L7 内核三处同步、L8 CAS 正确性、L13 实机 verify、/code-review 终裁 |
| 设计权威 | O8 全部实现语义 = [Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0（§4 摘要 / §5 W9 / §6 护栏 / §8 质量回路）；实施与设计冲突时**先升设计文档版本再动代码** |
| 收口守门 | `uv run pytest -q`（基线 **1075**/4 只增不减）· `pnpm -F @coagentia/web test`（**403** 只增不减）· `pnpm typecheck`（pyright 0）· `uv run ruff check .` · `pnpm gen` 后 diff 空（golden **58**+新 partial 判例）· `pnpm -F @coagentia/web build` |

## 1. 波次编排

### 块 M8a 加固批

| 波 | 模块 | 并行性 | 备注 |
| --- | --- | --- | --- |
| a-0 | **L0 契约落笔 + contracts 同步** | 单件先行 | A v1.0.12 / B v1.5.1 誊写（照汇总设计 §9）+ manifest/mock/gen；半天件 |
| a-1 | **L1 原子建边** ∥ **L4 残留收敛** ∥ **L6 展示面残窗** ∥ **B-M8-1 ②③④**（深链/遮挡/R-13 纯前端件） | 四路并行（canvas 路由 / hub·held_drafts / deploy 日志 / web 文件域不相交） | **L4a Fable 亲做**：先写"真适配器时序仿真"红测（ack 前发 status 上报撞锁）再动读循环结构；B-M8-1 ①（上游多选）等 L1 端点就绪接线 |
| a-2 | 块收口：全量守门 + 交接文档同步 + 阶段小结 | — | 出口 = 任务书 §9a #1–#6 |

### 块 M8b O8 编排质量线（块 a 收口后开工）

| 波 | 模块 | 并行性 | 备注 |
| --- | --- | --- | --- |
| b-1 | **L7 迁移 0012 + W9 内核** | 单件先行，**Fable 亲核** | 纪律 8 首改 graph 组：golden 判例先扩 partial 用例、py/ts 双跑守门立起，再让 strict 全量回归证明零变化；landing 默认 partial 一行随此波 |
| b-2 | **L8 汇总执行域** ∥ **L9 质量回路+话术** | 双路并行（hub/canvas 汇总域 / proposal 落地钩子+role_templates 文件域不相交） | L8 的 summary_runs CAS 与唤醒抑制双面 Fable 亲审；L9 复用 inject_guard_feedback 零新帧 |
| b-3 | **B-M8-2 O8 可见面** | L8 形状就绪后接线 | 横幅/badge/改档/阻断-恢复 |
| b-4 | 块收口：O8 真机场景（任务书 §9b #11，可与 L13 合并跑）+ 全量守门 | — | 出口 = §9b #7–#12 |

### 块 M8c 外壳与收官（块 b 收口后开工）

| 波 | 模块 | 并行性 | 备注 |
| --- | --- | --- | --- |
| c-1 | **B-M8-3 + L10 外壳** ∥ **L11 打招呼** | 双路并行 | 均为小件；L11 owner 可否决（任务书裁决 #9），否决即划去 |
| c-2 | **L12 编排体验收官** | 单件 | C1 多节点真机演示（番茄钟系扩展）+ 教程章节 + C2 delta UX 复盘 + C3 全链路收官——踩在 O8 已收口真机上 |
| c-3 | **L13 m8_verify 实机收口** | 单件，**Fable 亲做** | 加固批探针 + O8 全链 + 外壳真机 + 浏览器 E2E；证据归档 M8-EVIDENCE.md |
| c-4 | /code-review high 终收口（多维 finder → 对抗核实 → Fable 终裁）→ 任务书归档 | — | M6/M7 收口惯例 |

## 2. 防返工锚点（开工前读一遍）

1. **L4a 三不变量**：帧内顺序（同连接上报按到达序生效）、ack 语义（daemon 重传判据不变）、emit 时序（gateway_tx 提交后事件才出）——写队列改造若破坏任何一条即回退重设计。
2. **L7 "新增档不动旧档"**：upstream_policy 默认 'strict' 必须让全部既有测试与 golden 逐字节不变；partial 只以新判例进入。derive_blocked 签名变更须 py/ts 同一提交内齐改。
3. **L8 摘要幂等**：指纹未变不重发——否则每次唤醒刷一条系统消息，摘要消息自身又是唤醒触发，成自激振荡。指纹比对在发送判定处单点。
4. **L8 轮计数与既有投递解耦**：计轮挂在"因该汇总任务向 owner 投递唤醒"这一个点；不要在多个唤醒入口各自 +1（重复计数）。
5. **L1 复用步进原子形**：节点+入边一个事务；不要"先建节点再补边"两段式（那正是空成功窗口本身）。
6. **R-14 前端拼接**：以 chunk_seq 为唯一去重键；不要按行文本去重（日志行可重复合法）。
7. **O8 红例用 daemon-sim**：可控多轮唤醒/可控空转；真 CLI 只进 L12/L13 演示位。
8. **CR-M8 教训随身**：探针环境自带 env（PYTHONIOENCODING 等）会掩盖被测缺陷——L4 时序仿真要在"不带救生圈"的裸配置下跑。

## 3. 进度表（随模块完成更新）

| 模块 | 状态 | 完成提交 | 备注 |
| --- | --- | --- | --- |
| L0 契约落笔+登记 | ✅ 完成 | `3532b6a` | A v1.0.12 / B v1.5.1 落笔 + contracts（UpstreamPolicy/SummaryRun/upstream_node_ids/RULE_CODES O8·O9）+ gen 确定。**执行决策：迁移 0012（summary_runs + canvas_nodes.upstream_policy）与 ORM 随 L0 落地**（原 DEV-PLAN 置于 L7）——因 test_schema_conformance 反射 canvas_nodes 列集须与 CanvasNodeRow 同步，否则块 M8a 全绿门守不住；列默认 strict 行为逐字节不变、summary_runs 无运行期消费，纯 schema 前移零风险。**L7（M8b）改为只做 W9 内核双档 + golden partial + landing 默认 partial + patch_node 改档**（迁移已落）。守门：contracts 125 / 全量 1082 / gen 确定 / pyright 0 / ruff 净 |
| L1 原子建边 | ✅ 完成 | `3f002fd` | POST /nodes 消费 upstream_node_ids 同 tx 建节点+入边；悬空 422 全量收集 + 回滚；K1 空成功窗口回归转绿（携未完成上游即 blocked）。canvas 22 / gating·system_nodes·conformance 88 绿 |
| L4 残留收敛（a/b/c） | ✅ 完成 | `4cd79a8` | **L4a**：读循环收帧入队 + 独立 writer 消费（DB 写 offload 到 to_thread，不阻塞 loop、不撞锁撕连接）；_spawn 线程安全 + _system_pending threading.Lock；心跳写移出读循环；ack 语义/帧序/emit 时序保。**L4b**：discard 预检 agent_daemon_online + inject 挪 tx.after_commit。**L4c**：reevaluate 勘查确认已提交后（既有钉住测试 test_reevaluate_advances_read_position）。sync() 屏障语义调整（非 ack 上报改异步）+ drain_reports 屏障；daemon 55 / 全量 801 绿；+2 L4a 探针（写错不撕连接 / 阻塞写不阻塞读循环） |
| L6 R-10/R-14 + B-M8-1⑤ | ✅ 完成 | `ab84406` | **R-10**：deploy.log 去重游标 `<id>.seq` sidecar 持久化——server 重启内存游标丢失后从 sidecar 恢复去重，崩溃时序探针无重复行（test_deployments +1）；终态/fail-close 同清 sidecar。**R-14/⑤**：前端 deployLog 累积模型加 chunk_seq 单调去重 + 历史首页前 live 块进 pending 缓冲、首页并入按 seq 升序 flush（历史先于实时，消交叠重复）；DeploymentCard 改**先订阅后拉历史** + 失败兜底 flush；vitest 453→463。残留：某 chunk 落盘先于 GET 快照的精确交叠去重需 GET 暴露 max chunk_seq（升 B 契约），裁决 #8 暂挂 |
| B-M8-1 加固前端四件 | ✅ ①②③④ 完成（⑤随 L6） | `b8f9e64` | ①上游多选(配 L1)/②深链纠偏 resolveThreadChannelId/③.panel z-index 41 越 preview-deck/④R-13 resolveActingMember；vitest 444→453、web tsc 0。⑤ R-14 日志缓冲随 L6 提交 |
| —— 块 M8a 收口 —— | — | — | |
| L7 W9 内核（0012 已随 L0 落） | ✅ 完成 | `cb5d00b` | 内核 `derive_blocked` 扩双档（done_satisfied/terminal_satisfied + 节点 policy 映射，签名向后兼容 3 参=纯 strict 回归）py 权威 + lib/graph.ts 镜像 + golden 扩 5 partial 判例，双跑逐字节；`_satisfied_sets` 产双集合 + `_node_policy` + `_blocked_nodes` 单点（3 caller 收敛）；landing 汇总节点默认 `upstream_policy=partial`（一行）；**patch_node 人类改档**（O9 门挡 Agent 403）——**实施补形：NodePatch 扩可选 upstream_policy → B v1.5.1→v1.5.2**（L0 汇总设计 §9 漏列该字段，纪律 1 升版回改；设计 §5.2/裁决 #6 正文既定）+ 设计 v1.0→v1.0.1。守门：pytest **1094**/4（+5：kernel partial×3/contract shape/gating W9）· vitest **472**（+9：deriveBlocked golden partial + deriveCanvasBlocked 双档组装）· pyright 0 · ruff 净 · gen 确定（仅 NodePatch.upstream_policy）· tsc 绿 |
| L8 汇总执行域 | ✅ 完成 | `7d4c910`+`e68abcd` | 新模块 `orchestration/summary.py`：collect_summary_inputs（前驱联查 + 有界截断 ≤12 节点/≤5 deliverables/≤160 截断/≤8KB）+ render + summary_fingerprint（复用 fingerprint 内核）+ summary_runs CAS（ensure lazy / **advance_progress** 进展轮 fp 变才计+幂等 / **note_wakeup** 空转轮 fp 未变 stall++ / add_repeat_stall / **consume_replan** 预算 CAS / recover / _set_blocked CAS / post_coordination_block 单点）。轮计数**双入口**（防返工锚点 4 单点化的实施选择）：hub `_scan_channel_summary_nodes` 结构进展轮（task 终态[done+closed]/画布结构变/落地完成触发，发/追发摘要 @Orchestrator）+ delivery `_note_summary_wakeup` 无进展 stall 轮（非系统非人类唤醒）。gating 阻断双面（message_delivery_gated 扩 blocked_at 抑制）；恢复（人类线程发言[messages 路由]/force-start[tasks 路由] 同事务清 blocked_at+归零，replan 不重置）；**replan 第 2 次 403 rule=O8**（proposal `_guard_summary_replan`，classify+apply 路径）+ 重复 delta 加倍（`_apply_delta_active_exists`）。新增诊断 `summary.coordination_blocked`（constants，server 内部非 gen）。守门：pytest **1111**/4（+17：test_summary 15[CAS/纯逻辑/scan 幂等/gating 抑制] + test_delta 2[replan 403/首次消费]）· pyright 0 · ruff 净 · gen 确定。**F6 崩溃续算/CAS 竞态由持久 summary_runs+原子 CASE UPDATE 内在保证，全链真机演示归 L13**。轮语义细节（scan 进展/delivery 空转分工）见正文注释 |
| L9 质量回路+话术 | ✅ 完成 | `872762a` | **话术**：role_templates builtin +2 节（第 9 汇总职责[摘要触发/未覆盖逐条照抄 W9/report 发频道主流/In Review/replan 预算 O8/提问也是合法产出] + 第 10 质量信号[先复述再沉淀 MEMORY.md/同类两次视为习惯问题]），启动 upsert 幂等（既有 test_role_template_upsert_idempotent 覆盖）。**质量回路**：新模块 `orchestration/quality.py`（`adjustment_signal_body` 单源信号体：landed_hash≠proposal_hash 或 adjustments 非空 → 结构化[decomp 调整数/delta 剔除下标]，未调整 → None 防噪声）；landing `_finish_landed`（decomp+delta 共用单点）带调整落地 → source 线程质量信号系统消息 @proposer（durable 留痕 + @唤醒学习）+ hub LANDING_COMPLETED 提交后 `_signal_landing_quality` GUARD_FEEDBACK 直投（async send_instr，daemon 离线静默）。**REJECTED 触发点按 M6b 教训仅被动留痕不主动直投**（draft.reject_proposal 既有；主动唤醒诱发未请求重提）——此分野是 §8.2 与 M6b 教训合流的实施裁决。守门：pytest **1116**/4（+5：test_quality 4 + role_templates L9 1；test_delta 带调整落地断言扩展）· pyright 0 · ruff 净。O7 第三败不重复发信号 = 既有 failed_escalated 路径不变（未新增信号面） |
| B-M8-2 O8 可见面 | ✅ 完成 | `a51637c` | **纯前端 + 1 处 wsBridge**（零后端、零契约、零迁移）。单源解析模块 `lib/summary.ts`（后端护栏消息体字面量契约的唯一镜像：`render_summary_message`/`post_coordination_block`/force-start 锚点/`adjustment_signal_body`）：`parseSummaryHeader`/`summaryCoverCounts`/`classifySummaryLines`/`parseBlock`/`isForceStartAnchor`/`deriveO8Banner`（横幅态机：摘要→active、阻断→blocked、阻断态遇人类发言/force-start→清横幅，下一轮摘要重亮）。**① 横幅**（`SummaryBanner`）三态：active 轮数 N/8+未覆盖 chip、blocked 原因(轮数/stall)+计数+「强制启动以恢复」（复用 `ForceStartModal`，force-start 同事务 recover）；挂 ThreadPanel（`items` 派生，非汇总线程→null 隐去）。**② badge**（`buildCanvasModel`+`TaskNodeCard`/`SystemNodeCard`）：汇总节点「汇总」+ partial 档「partial」。**③ 检视器**（`NodeInspector`，画布选中浮层）：`upstream_policy` strict↔partial 段控件 → `patchCanvasNode`（O9 门 server 挡 Agent），`canvas.node_updated` 反流刷新。**④ 卡片**（`SummaryCard`，MessageFlow 系统消息按体首行头识别 → 结构化）。**wsBridge**：threaded `message.created` 失效 `qk.thread`（qk.thread 原无 WS 失效——O8 系统消息落线程后横幅/卡片靠此实时刷新，顺带收敛线程回复实时性）。守门：vitest **472→502**（+30：summary 16/banner 4/card 2/canvas-o8 6/wsBridge 2）· web tsc 0 · pyright 0 · ruff 净 · gen 确定 · build 绿。截图对照 = 独立 story 渲染四件（真主题）经 get_page_text/read_page 核实（截图工具本机超时，DOM 内容已证）。O8 真机全链演示归 L13 |
| —— 块 M8b 收口 —— | ✅ 收口 | `a51637c` | B-M8-2 收口守门全绿 + 交接文档同步（本表/CURRENT-HANDOFF/PROJECT-RECORD）。§9b #11「O8 真机全链」按任务书「可并入 L13」延至 M8c。接续 = 块 M8c（B-M8-3 外壳 + L10/L11/L12/L13） |
| B-M8-3+L10 外壳 | ✅ 完成 | （c-1 待提交） | **纯前端 + L10 零新端点**（POST /channels 后端既完备，L10 预判成立）。①Members 页「创建 Agent」按钮（`.ms-head` h1 flex:1 右顶）→ CreateAgentModal；②SetupChecklist 步骤 002 `onAction` 挂 CreateAgentModal（既有 ToastProvider 内）；③侧栏「新建频道」死壳 → `NewChannelModal`（name/desc/private，POST /channels，NAME_TAKEN 就地报错）+ `api.createChannel` + `useCreateChannel`（镜像 useCreateDm，invalidate channels）。子代理执行、主循环 review diff（`.ms-head` flex 核实、NewChannelModal 空成功语义、member_ids=[] 语义）。vitest 502→512（+10：NewChannelModal 5/ChannelList 2/MembersScreen 1/SetupChecklist 2）· web tsc 0 · build 绿 |
| L11 打招呼（默认关） | ✅ 完成 | （c-1 待提交） | owner 拍板**做**（goal「做打招呼」）。**复用既有 workspace.onboarding_greeting 开关**（原死壳无消费者，WorkspaceSettingsModal 已在用）；**默认关（裁决 #9）= 改 models.py server_default text(0)**（0001 由 metadata.create_all 建表，故此一处即所有新库 DDL 默认关）+ seed.json false + 契约 entities 默认 False（rest.ts @default false，gen 确定）。**触发点** = lifecycle START 成功后（`_maybe_onboarding_greet`）：双门（开关 + diagnostic 幂等标记 `agent.onboarding_greeting` 本地类型，同 `_DIAG_REMINDER_CANCELLED` 体例）→ 写标记（提交前）+ tx.after_commit best-effort 直投 `hub.inject_onboarding_greeting`（InjectKind.SYSTEM，离线静默，铁律 4）。**重启不重复**靠标记 airtight。pytest 1117→1122（+5：默认关零动作/开→一条/重启不重复/预检离线不落标记/failed 不动）· pyright 0 · ruff 净。真机问候归 L13 |
| L12 体验收官+教程 | 未开工 | — | |
| L13 m8_verify 收口 | 未开工 | — | Fable 亲做 |
| /code-review 终收口 | 未开工 | — | |
