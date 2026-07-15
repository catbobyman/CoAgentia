# M8 实用化加固、编排质量与单机产品化 —— 任务书（M8-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-14，MVP（M1–M7）收口 + CR-M8 真机双修（HEAD `62832f4`）后立项；**取代 [M8-SCOPE-DRAFT.md](M8-SCOPE-DRAFT.md) v0.1**（其波次盘点与实测证据并入本文，草案头部已标注废止） |
| 用途 | **M8 里程碑的唯一任务书入口**。M8 是 MVP 后首个非 PRD 里程碑：范围 = owner 2026-07-14 两轮拍板（§7 #1/#2）——**加固批**（真机实测缺陷 + CR-M8-1 同族残留 + 展示面残窗）+ **O8 编排质量线**（[Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0 兑现）+ **单机产品化外壳** + **编排体验打磨与教程收官** |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 六契约（A v1.0.11 / B v1.5 / C v1.0 / D v1.0.5 / E v1.5 / E2 v1.0.1；本次修订摘要 §1）· **[Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0（O8 实现级权威）** · [M7-RESERVATION-AUDIT.md](../M7-RESERVATION-AUDIT.md) R-10/R-13/R-14 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) §5 挂账（CR-M8-1 残留）· 2026-07-14 全链路真机实测缺陷账（SCOPE-DRAFT §2 波 1，实锤复现） |
| 执行计划 | [M8-DEV-PLAN.md](M8-DEV-PLAN.md)（波次编排 + 进度表）；协作模式 = [COLLAB-MODEL.md](COLLAB-MODEL.md) v2 续用（Fable 单窗编排；内核/CAS/hub 并发重构 Fable 亲做，执行/评审派子代理） |
| 出口标准 | §9 三块清单全绿：加固批回归零复现 → O8 真机全链（摘要→报告→护栏红例）→ 新用户不看源码走完全链路（外壳）+ 教程收官 |

---

## 0. 一句话目标

MVP 证明了"从对话到上线"能走通；M8 让它**经得起用、看得出好**：把真机实测暴露的缺陷与同族并发残留收干净（多 Agent 强度下不死锁不空成功）、给 Orchestrator 的汇总职责装上输入与护栏（O8：拆完还要收得拢、循环有顶）、补齐单机产品化外壳（新用户不看源码建 Agent 走全链）、用一次真实多节点拆解把教程收官。

## 1. 契约修订摘要（**L0 开工第一步落笔**；内容已在设计文档字段级定案，此处为登记）

> 与 M7"立项即落笔"的差异：本次 O8 契约内容全部源于汇总设计 §9（字段/表/语义已定案），立项会话产出设计文档本体；文档誊写 + contracts 包同步合并为 L0 一步完成，避免设计-契约双份手抄漂移。owner 如要求"先落笔再开工"，L0 单独先行即可，不影响切分。

| 契约 | 目标版本 | 修订内容（出处 = 汇总设计 §9） | 状态 |
| --- | --- | --- | --- |
| A 实体表 | v1.0.11 → **v1.0.12** | ① 新表 `summary_runs`（汇总协调状态：三计数/指纹/blocked_at，直挂 workspace_id）；② `canvas_nodes.upstream_policy`（'strict'\|'partial'，默认 strict）；③ 迁移批次 **0012**（M8 单批） | 待 L0 落笔 |
| B REST | v1.5 → **v1.5.1** | ① `POST /canvases/{id}/nodes` 请求体扩**可选** `upstream_node_ids[]`（K1 方案 A：同事务原子建节点+入边；向后兼容）；② replan 超额 = **403 rule=O8**（O9 先例形状，**错误码目录零新增仍 29**）；③ 零新端点 | 待 L0 落笔 |
| C WS | — | **零修订核对**：摘要/阻断/恢复走 message.created / task.updated / diagnostic 既有事件族 | L0 核对 |
| D daemon | — | **零修订核对**：唤醒/投递/GUARD_FEEDBACK 直投全既有帧；加固批 L4 若动帧时序须回核（预判不动帧模型） | L0 核对 |
| E / E2 | — | **零修订核对**：零工具新增；Orchestrator builtin 话术 +2 节（汇总职责/质量信号）属角色模板数据非契约面（M6b J11 先例，话术权威 = 汇总设计 §8.3） | L0 核对 |
| 内核（纪律 8） | graph 组 | **W9 双档 satisfied 语义变更**：kernel/graph.py 权威 + lib/graph.ts 镜像 + golden 扩 partial 判例，双跑逐字节 | L7 实施 |

## 2. 范围与非目标（勿扩界）

**范围**（owner 拍板 §7 #1/#2）：

- **加固批**（块 M8a）：① 手动系统节点空成功竞态（**方案 A 原子建边**，拍板 #2）；② 线程深链挂起；③ 预览面板遮挡交互；④ CR-M8-1 同族残留收敛（hub 读循环同步写 DB 移出 + held discard 事务内 inject 挪提交后；reevaluate 经勘查**已在提交后**，补测试钉住即可）；⑤ R-13 部署弹窗触发者；⑥ R-10 deploy.log 去重游标持久化 + R-14 日志重开交叠（前端缓冲拼接优先，零契约）。
- **O8 编排质量线**（块 M8b）：汇总设计 v1.0 全量兑现——0012 迁移 + W9 部分失败（内核三处同步）+ 有界状态摘要 + 协调循环护栏（轮/stall/replan/阻断/恢复）+ 拆解质量回路 + 角色话术两节 + 前端可见面。
- **单机产品化外壳 + 编排体验收官**（块 M8c）：建 Agent 通用 UI 入口（Members 页复用 CreateAgentModal）/ 首跑清单「创建 Agent」按钮挂接 / 侧栏新建频道实装核对 /（默认关开关的）新 Agent 入频道打招呼；多节点 decompose 真机演示 + 教程章节 / delta 审查面 UX 复盘 / 教程全链路收官。
- **0012 建表批次**（A §5）：summary_runs + canvas_nodes.upstream_policy 一批。

**非目标**（SCOPE-DRAFT §3 原样 + 汇总设计非目标）：多用户/登录/邀请（M9 候选，auth 底座先行）· 多租户/多机/多副本（R-1~R-9/R-11/R-12/R-15 维持挂账）· runtime 第 3 种 CLI · OAuth 生态 · PWA/移动端 · 消息动作全集 · 汇总报告 schema 强制 · 质量信号聚合读面 · 递归拆解独立机制（汇总设计 §7 裁决：delta 表达）· 多 Orchestrator 抢占语义。

### 2.1 执行切分（三块竖切，体例同 M2–M7）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M8a 加固批** | 先收 | L0 · L1 · L4 · L6 ＋ B-M8-1 | 真机多 Agent 强度下的**正确性地基**：实锤缺陷零复现、同族死锁面收干；后续两块的演示都踩在它上面 |
| **M8b O8 编排质量线** | 块 a 收口后 | L7 · L8 · L9 ＋ B-M8-2 | Orchestrator 从"能拆"到**"收得拢、拆得好"**：汇总有输入、循环有顶、失败有放行、调整有回流 |
| **M8c 外壳与收官** | 块 b 收口后 | L10 · L11 · L12 · L13 ＋ B-M8-3 | **产品化收口**：新用户不看源码走完全链路；一次真实多节点拆解进教程（顺带成为 O8 的真机验收场） |

- 切分依据：块 a 是**并发正确性**心智（锁/事务/竞态），块 b 是**编排语义**心智（内核/护栏/注入），块 c 是**产品面**心智（入口/文案/教程）——三类心智不混批；且块 c 的真机演示天然消费块 a/b 的成果。
- 模块编号 **L 系列**（J=M6、K=M7 已占用；SCOPE-DRAFT 的 K1–K5 编号废止，映射见 §4 各行标注）。

## 3. 现有资产盘点（拿来即用，勿重复建设；2026-07-14 立项实核）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| after_commit 机制 + 参照修复 | deps.py:55/82（Tx.after_commit：仅提交成功路径、事件 flush 后按序执行）；proposals.py:138/159（预检 `agent_daemon_online` + after_commit 投递）；proposal.py:1464 flush_injects | L4 held discard 照此修法零新机制；测试先例 test_decompose_inject_fires_after_commit |
| gateway_tx | gateway_tx.py:32-48（同步 connect+begin，无 after_commit 钩子） | L4 hub 读循环改造的现状边界：读循环内 ~10 处 `_report_*` 全走它（hub.py:597 心跳裸 engine.begin / 612 分发 / 648·672·737·791·940·992·1079·1107·1122·1165） |
| 落地步进原子路径 | landing.py 步进 runner（每步=节点+其全部入边一个 gateway_tx，M6b J9） | L1 方案 A 原子建边**同构复用**——手动建节点带 upstream_node_ids 即单步落地形 |
| 汇总节点落地 | landing.py:238（追加条件）/586（_apply_create_summary_node，is_summary=True，owner 直写提案 Orchestrator） | L7/L8 的挂接点：policy 默认值一行、summary_runs lazy 建行时机 |
| gating 判定单源 | kernel/graph.py:61-72 derive_blocked + canvas/service.py:126-150 `_satisfied_nodes`（现状仅 done/success）+ 投递双面（hub.py:1358/1484） | L7 W9 双档改造的确切落点；纪律 8 三处同步 |
| TaskHandoff 单任务读面 | contracts/service.py active_contract/active_contracts + HANDOFF_REQUIRED_FIELDS 单源 | L8 collect_summary_inputs 逐前驱预取（≤12 节点，联查防 N+1） |
| 质量信号账本字段 | models.py:719-755 proposals（proposal_hash/landed_hash/adjustments=removed_ops/repair_count）；拒绝理由在 diagnostic_events.payload | L9 零建表——**字段全齐无人消费**，只写生成与投递 |
| 纠正信号直投通路 | hub.py:2233 inject_guard_feedback（InjectKind.GUARD_FEEDBACK，D §5.2 既有）+ 2210 inject_orchestrator | L9 投递零新帧；L4 注意其 `_run_sync` 等 ack 特性（修 discard 时机即可，通路本身不动） |
| fingerprint 内核 | 纪律 8 第二组（py 权威+ts 镜像+golden） | L8 summary_fp 复用，不造第二套指纹 |
| 条件 UPDATE CAS 惯例 | PROPOSAL_TRANSITIONS._transition / deployments 终态 CAS 等全库先例 | L8 summary_runs 三计数推进照抄 |
| CreateAgentModal + 角色模板段 | web 组件（M6b 拆解引导链在用） | L10 B1/B2 纯复用零新端点 |
| deploy.log 链路 | hub.py:940 _report_deploy_log（提交后落盘+游标推进，M7 收口修 ②）+ GET log 翻页 + ws 订阅流 | L6 R-10 sidecar 持久化 / R-14 前端拼接的现状基座；ws deploy.log 帧携 chunk_seq（D §7）——前端去重可行 |
| 真机 verify 基建 | scratchpad/m6a_harness.py + m6a_appfactory.py + m7b_verify.py 范式（隔离库/真 daemon-sim/scratch git/taskkill 杀树） | L13 扩 m8_verify；O8 护栏红例需 daemon-sim 可控多轮唤醒 |
| 使用教程 | `使用教程/` 图文 19 图（真实拆解流程截图） | L12 C1/C3 在其上扩全链路章节 |

**确认缺口**（施工面）：hub 读循环无写队列/writer task 先例（L4a 全新并发结构，Fable 亲做）· summary_runs 表/摘要拼接/护栏判定全新 · kernel graph 双档 satisfied 是**首次改第一组内核语义**（此前 M3b 建立后未动过）· SetupChecklistScreen:35 按钮无 onAction · deploy.log `.seq` sidecar 无先例。

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| L0 | **M8a** | 契约落笔 + contracts 登记同步（**开工第一步**） | §1 全表：A v1.0.12 / B v1.5.1 文档落笔；contracts 包：SummaryRun 模型、CanvasNode 扩 upstream_policy、NodeCreate 扩 upstream_node_ids、rule=O8 登记；C/D/E/E2 零修订核对记录；mock 补形状（纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` diff 空；六契约版本行与 README 速览同步 |
| L1 | **M8a** | 手动系统节点原子建边（SCOPE-DRAFT K1，**方案 A**，拍板 #2） | `POST /canvases/{id}/nodes` 消费 `upstream_node_ids`：同一写事务建节点+全部入边（复用落地步进原子形）；无该字段时行为不变（向后兼容）；系统节点认领扫描对"无入边新建系统节点"的窗口由原子建边根治 | 带上游建 Merge 节点 → 不出现空成功（回归复现例转绿）；无上游创建路径不变；入边引用悬空/成环 → 422 全量收集错误；O9 面核对（Agent 仍 403） |
| L4 | **M8a** | CR-M8-1 同族残留收敛（CURRENT-HANDOFF §5；铁律 4「跨进程同步等待不得跨持锁事务」+「读循环不持锁写」） | **L4a** hub 读循环写 DB 移出：`_reader` 链上全部 `_report_*`（§3 盘点 10 处 + 心跳）改"读循环只收帧入队，独立 writer task 消费写库"（或等价：报文处理 offload 出事件循环），保帧内顺序、保 ack 语义、保 gateway_tx emit 时序；**L4b** held_drafts.py:186 discard 的 inject_guard_feedback 挪 `tx.after_commit` + `agent_daemon_online` 预检（照 proposals.py:138/159 修法）；**L4c** reevaluate 勘查已在提交后（hub.py:2336）——补钉住测试防回归 | L4a：真适配器时序仿真（ack 前发 status 上报）不再撞锁撕连接；帧序/ack/emit 回归全绿；busy_timeout 注入探针不阻塞读循环；L4b：discard 撞锁自死锁复现例转绿 + after_commit 测试（照 test_decompose_inject_fires_after_commit 体例）；L4c：钉住测试 |
| L6 | **M8a** | 展示面残窗（R-10 / R-14） | **R-10**：deploy.log 去重游标持久化——`<id>.log` 旁 `.seq` sidecar（或文件行数恢复），server 重启后 daemon 重传不再重复落行；**R-14**：日志重开进行中部署——前端先订阅缓冲→GET 历史→按 chunk_seq 拼接去重（**零契约**，ws 帧已携 chunk_seq；若实测发现流不携 seq 则升 C 契约回改——预检已确认 D §7 携带） | R-10：崩溃时序探针（落盘后/ack 前重启）无重复行；R-14：重开 running 部署无乱序/丢帧（前端行为测试 + 真机复验）；主路径（新建卡/终态卡）行为不变 |
| L7 | **M8b** | 0012 迁移 + W9 内核（汇总设计 §5） | Alembic `0012_m8`：summary_runs 一张（直挂 workspace_id）+ canvas_nodes 加列 upstream_policy 默认 'strict'（if_not_exists 纪律）；**kernel graph 双档 satisfied**：py 权威 + lib/graph.ts 镜像 + golden 扩 partial 判例（纪律 8 三处同步，双跑逐字节）；`_satisfied_nodes` 产出双集合；汇总节点落地默认 partial（landing.py 一行）；patch_node 人类可改档、Agent 403 rule=O9 同口径 | 双路迁移绿 + 表对照测试；golden 双跑逐字节含 partial 新例；strict 语义全量回归零变化（默认值兜底）；partial 放行/未覆盖推导逐例；O9 面红例 |
| L8 | **M8b** | 汇总执行域（汇总设计 §3/§4/§6） | `collect_summary_inputs`（前驱联查预取 + 有界截断规则）；摘要系统消息（首次解除必发/指纹变化追发/未变不重发）；summary_runs lazy 建行 + 三计数条件 UPDATE 推进；轮=唤醒投递计数（人类发言不计且重置 stall）；stall 指纹（复用 fingerprint 内核）+ 重复 delta 加倍；触顶阻断（blocked_at 置位 + 自动唤醒抑制双面 + @人类系统消息 + diagnostic）；恢复（人类线程发言 / force-start → 归零清 blocked_at，replan_used 不重置）；replan 预算（汇总期第 2 次 delta → 403 rule=O8 + @人类） | §11 验收 1–4 逐条：摘要内容/时机断言、8 轮触顶红例、stall 3 次红例、加倍计数例、replan 403 例、恢复例、崩溃续算例（F6）、终态失效例（F8）；CAS 竞态例（并发唤醒恰一次计数） |
| L9 | **M8b** | 质量回路 + 话术（汇总设计 §8） | 落地带调整（landed_hash≠proposal_hash 或 adjustments 非空）/ 提案 REJECTED → 结构化信号：inject_guard_feedback 直投 + 线程系统消息留痕；role_templates builtin +2 节（第 9 汇总职责 / 第 10 质量信号），启动 upsert 幂等 | 两触发点各一例（信号内容断言：调整摘要/removed_ops/拒绝理由）；O7 第三败不重复发信号；upsert 幂等（双启动不重复）；话术进注入体断言 |
| L11 | **M8c** | 新 Agent 入频道打招呼（SCOPE-DRAFT B4，默认关；owner 可否决 §7 #9） | Agent 创建完成且开关开启 → daemon 起跑注入一次性问候指令（PRD FR-1.4 语义：入 #all 用工作区主导语言打招呼；可开关，默认关）；开关落 Agent 创建面或工作区设置（就近 P12 体例） | 开关开→真机问候一条；关→零动作；重启不重复问候（一次性幂等） |
| L13 | **M8c** | 端到端实机 verify（**M8 收口**） | m8_verify.py（m6a_harness 体例）：加固批探针（K1 空成功复现转绿 / L4 时序仿真 / R-10 崩溃探针）+ **O8 全链**（多节点 decompose → 并行交付 → 一节点 Close → partial 放行 → 摘要消息 → 总报告含未覆盖 → 护栏红例三条 → 恢复）+ 外壳真机（Members 页建 Agent 上线干活）+ 浏览器可视 E2E（B-M8 三件） | §9 三块清单收口 + 证据归档 `docs/verify/M8-EVIDENCE.md`（截图/JSON 双证） |

**推进顺序**：**块 M8a**：L0 → L1 ∥ L4 ∥ L6（文件域不相交：canvas 路由 / hub·held / deploy 日志）＋ B-M8-1 随行；**块 M8b**：L7（内核+迁移先行，纪律 8 三处同步 Fable 亲核）→ L8 ∥ L9 → B-M8-2；**块 M8c**：L10 ∥ L11 ∥ L12 → L13 收口。

## 5. 线 B：前端任务分解（模块 → DoP）

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M8-1 | **M8a** | 加固批前端四件 | ① SystemNodeModal 上游多选（配 L1，POST 携 upstream_node_ids）；② 线程深链修复（`?thread=` 直开 connecting… 挂起——深链路径补频道上下文装载，SCOPE-DRAFT K2）；③ 预览面板遮挡修复（.preview-deck 指针事件拦截线程按钮，z-index/布局修，SCOPE-DRAFT K3）+ 预览开启时线程可交互回归；④ R-13 部署弹窗触发者取 acting member 惯用式（CanvasTab.tsx:564 改 me 取法并统一）；⑤ R-14 日志缓冲拼接（配 L6） | 行为测试逐件 + Playwright 实测：深链直开可交互 / 预览开启点线程按钮成功 / 带上游建节点 UI 链路 |
| B-M8-2 | **M8b** | O8 可见面 | ① 汇总任务线程横幅：轮数 `N/8`、stall 状态、**协调阻断横幅**（原因 + 计数事实 + 「恢复」引导=在线程发言或 force-start，复用既有 override 按钮）；② 画布汇总节点 badge（is_summary 已有数据 + partial 档标识）；③ 节点详情 upstream_policy 展示与人类改档控件（patch_node 通道）；④ 摘要系统消息卡片化渲染（结构化 markdown 段落体例）；wsBridge 接 task.updated/diagnostic 既有族 | 行为测试（横幅三态/badge/改档防呆/阻断-恢复链路）+ 截图对照 |
| B-M8-3 | **M8c** | 产品化外壳（SCOPE-DRAFT B1–B3） | ① Members 页「创建 Agent」按钮复用 CreateAgentModal（含角色模板段；可选：机器详情页同挂）；② SetupChecklistScreen:35 「创建 Agent」步骤挂接同弹窗；③ 侧栏「新建频道」实装（**2026-07-14 已核对：常态壳同为装饰件——ChannelList.tsx:80 无 onClick；POST /channels 后端完备 → 纯前端建弹窗接线，L10 零新端点预判成立**）；④ L11 打招呼开关的创建面挂接 | 真机：不经拆解引导链、从 Members 页建成一个可上线干活的 Agent；首跑清单步骤按钮全部有动作；新建频道两态可用 |

**L10 = B-M8-3 的后端配合件**（预计零新端点，若核对发现新建频道缺失端点则按 B 契约既有形状补）；**L12 = 编排体验收官**（前后端联合）：C1 多节点 decompose 真机演示（题材=番茄钟系扩展，裁决 #10）+ 教程新章节（依赖边/merge 汇总/并行认领/**O8 摘要与总报告实录**）；C2 delta 审查面 UX 复盘（与 B-M8-1 ③ 遮挡修复联动复验）；C3 教程全链路收官（拆解→确认→写码→交接→Diff→合并→预览→部署→成本→**汇总报告**）。

## 6. 纪律（沿用 M2–M7 八条，本里程碑强调四点）

1. 契约 ↔ manifest 双向同步（L0 收口中间态；实施发现形状问题升版回改——M5 H2/M6 J3/M7 K2 先例）。
2. **纪律 8 首次触及 graph 组语义**（W9 双档 satisfied）：py 权威 + ts 镜像 + golden 三处同步缺一即红；strict 默认值必须让全量既有行为逐字节不变——"新增档位，不动旧档"。
3. **CAS 纪律**（M6 三度印证 + CR-M8-1 教训合流）：summary_runs 计数推进全条件 UPDATE；**读循环不持锁写、跨进程同步等待不得跨持锁事务**——L4 是该铁律的收尾工程，修完后全库应无"事件循环上同步 gateway_tx"残留（L4 DoD 含全链扫描断言）。
4. **护栏可见**（D4 哲学延伸到 O8）：摘要/阻断/恢复全部走系统消息进账本，人机同源；不做只有 Agent 看得见的暗信道。
5. 每完成一模块更新 M8-DEV-PLAN 进度表 + CURRENT-HANDOFF；阶段结论沉淀 PROJECT-RECORD；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问（§7 #1/#2 即拍板项）。
7. 值域/判定语义只写一处：放行策略判定活在 kernel+_satisfied_nodes 单点；摘要拼接/截断规则活在 collect_summary_inputs 单点；前端只消费不复算。
8. 实机验证进程结束前必杀（taskkill /F /T）；O8 护栏红例用 daemon-sim 可控多轮唤醒，勿拿真 CLI 烧 token 空转。

## 7. 本任务书裁决（#1/#2 = owner 拍板 2026-07-14；#3–#8 = 汇总设计 §12 裁决表并入；其余 owner 可否决，否决处升版回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | **范围 = 加固批 + O8 编排质量线 + 波2 产品化外壳 + 波3 编排体验打磨**；多用户/多租户/多机/多副本均押后 | owner 2026-07-14 两轮拍板 |
| 2 | **K1（→L1）修法 = 方案 A 原子建边**（POST nodes 扩 upstream_node_ids，与 delta 落地步进同构） | owner 2026-07-14 |
| 3 | O8 摘要/阻断走线程系统消息（C/D 零修订）；轮=唤醒投递计数、人类发言不计轮且重置 stall；上限 8/stall 3/replan 1 先常量单点 | 汇总设计 §12 #1–#3 |
| 4 | replan 超额复用 403 rule=O8（错误码仍 29）；恢复不重置 replan_used | 汇总设计 §12 #4/#8 |
| 5 | 汇总节点落地默认 upstream_policy='partial'、普通节点 strict；delta 节点内形不携带 policy（放行宽松化归人类 patch_node） | 汇总设计 §12 #5/#6 |
| 6 | 递归拆解不建独立机制（delta 表达 + 话术显性化）；质量信号即时直投不聚合、MEMORY 归 Agent 自管 | 汇总设计 §12 #7/#9 |
| 7 | **L4a 修型 = 读循环收帧入队 + 独立 writer 消费**（保帧序/ack/emit 时序）而非逐处 to_thread 撒点——同族问题一次收干，避免第 11 处复发 | 铁律 4 收尾工程；CR-M8-1 教训 |
| 8 | R-14 修型 = 前端订阅缓冲 + chunk_seq 拼接去重（零契约）；实测若 ws 流无 seq 才升 C 契约 | D §7 帧已携 chunk_seq；最小面 |
| 9 | L11 打招呼进本批但**默认关**（PRD FR-1.4 语义兑现；SCOPE-DRAFT 待拍板 #2 的默认处置） | 成本小感知高；owner 可否决整件 |
| 10 | C1 演示题材 = 番茄钟系扩展（教程连续性、scratch 基建现成） | SCOPE-DRAFT 待拍板 #3 的默认处置；owner 可换题 |
| 11 | 契约落笔归 L0 开工第一步（内容已字段级定案于汇总设计 §9），非立项即改 | §1 说明；owner 可要求 L0 先行单独收口 |

## 8. 挂账（承接 CURRENT-HANDOFF §5 + M7-RESERVATION-AUDIT；勿当漏项重新发明）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| ~~CR-M8-1 同族残留~~ | ~~hub 读循环写 DB / held discard 事务内 inject~~ | **L4 收**（本任务书） |
| ~~R-10 / R-13 / R-14~~ | ~~deploy.log 游标 / 弹窗触发者 / 日志交叠~~ | **L6 / B-M8-1 收** |
| R-1~R-9 / R-11 / R-12 / R-15 | 多租户/多机/多副本/注入面中和/queued 残窗/孤儿清扫/缓冲 success 竞争 | 维持挂账（M9+ 按批立项；非目标 §2） |
| M6b CR-10 / CR-11 | 索引谓词双源 / 0009 downgrade FK | 观察项/择机（0012 落地时 CR-10 同型断言顺路核对） |
| M5b–M2 承接 | briefing @全部 / `_layout_positions` / held 倒计时 / `task #n` refs / `<TaskBoard>` / messages_fts / OAuth 冷启动 | 原归属不变（L13 真机可顺路复验 OAuth，未做则延续） |
| 模板域 | 模板不携 system 节点 / 无 Project 重映射 | MVP 观察项 |
| **本任务书新增** | 子画布/跨画布递归展开（汇总设计 §7）· 质量信号历史聚合面 · O8 阈值频道级可配 · 多 Orchestrator 抢占 | 观察项（需求出现再议） |

## 9. M8 出口验收清单（按块分组；三块全绿即里程碑收口）

### 9a. 块 M8a「加固批」

- [ ] 1. L0 契约落笔与登记：A v1.0.12 / B v1.5.1 / C·D·E·E2 零修订核对，manifest+mock+gen 全绿
- [ ] 2. L1 原子建边：空成功复现例转绿；无上游路径不变；悬空/成环 422；O9 面不变
- [ ] 3. L4 三件：读循环时序仿真不撕连接；discard 自死锁复现例转绿 + after_commit 测试；reevaluate 钉住；全链扫描无"事件循环同步 gateway_tx"残留
- [ ] 4. L6：R-10 崩溃探针无重复行；R-14 重开 running 部署无乱序丢帧
- [ ] 5. B-M8-1 四件：深链直开可交互 / 预览开启线程可点 / 上游多选 / R-13 触发者——Playwright 实证
- [ ] 6. 块 a 守门：全量测试（基线 1075+403 只增不减）/ typecheck / ruff / gen / build 全绿；交接文档同步

### 9b. 块 M8b「O8 编排质量线」

- [ ] 7. L7：0012 双路迁移绿；golden 双跑逐字节含 partial 新例；strict 全量回归零变化；O9 面红例
- [ ] 8. L8 护栏全红例：摘要（必发/追发/不重发）/ 8 轮触顶 / stall 3 次 / 重复 delta 加倍 / replan 403 rule=O8 / 恢复归零 / 崩溃续算 / 终态失效 / CAS 竞态
- [ ] 9. L9：两触发点信号 + O7 不重复 + upsert 幂等 + 话术注入断言
- [ ] 10. B-M8-2：横幅三态 / badge / 改档 / 阻断-恢复链路，行为测试 + 截图
- [ ] 11. **O8 真机场景**（可并入 L13）：多节点拆解 → 一节点 Close → partial 放行 → 摘要消息 → Orchestrator 总报告**含未覆盖标注** → 制造空转 → 阻断 @人类 → 人类发言恢复
- [ ] 12. 块 b 守门同 #6

### 9c. 块 M8c「外壳与收官」

- [ ] 13. B-M8-3 + L10：Members 页建 Agent 真机上线干活；首跑清单按钮全有动作；新建频道两态可用
- [ ] 14. L11：打招呼开关开/关/幂等三例（若 owner 否决则划去）
- [ ] 15. L12：多节点 decompose 全链路真机截图进教程；delta UX 复盘结论落档；教程全链路章节收官
- [ ] 16. **L13 收口**：m8_verify 全探针 ALL PASS + 浏览器可视 E2E；证据归档 `docs/verify/M8-EVIDENCE.md`
- [ ] 17. 终收口守门：全量绿；阶段结论写入 PROJECT-RECORD；本任务书移入 archive/（README 约定 3）；SCOPE-DRAFT 一并归档

## 10. 第一步建议

块 M8a 从 **L0 契约落笔**开工（内容照汇总设计 §9 誊写 + contracts 同步，半天件）；随后 **L1 ∥ L4 ∥ L6 三路并行**（canvas 路由 / hub·held / deploy 日志文件域不相交）——其中 **L4a 读循环改造 Fable 亲做**（并发结构变更，撕连接/ack/emit 时序都是一等风险面，先写"真适配器时序仿真"红测再动结构）；B-M8-1 的 ②③④ 纯前端件可与后端并行即刻开工。块 M8b 等块 a 收口后从 **L7 内核先行**（纪律 8 三处同步是全里程碑最需要慎重的一步——golden 判例先扩、双跑守门立起来，再写 L8 消费）。块 M8c 收官顺序：外壳件先行、L12 演示最后（让它踩在 O8 已收口的真机上，一次演示同时是教程素材与 O8 验收）。
