# M7 预留位审查文档（M7-RESERVATION-AUDIT）

| 项 | 内容 |
| --- | --- |
| 文档类型 | K8 交付件（M7-HANDOFF §4 K8 / DoD「盘点成文交 owner 过目，零代码变更」）——**审查不实施** |
| 版本 / 日期 | v1.0 / 2026-07-13（块 M7b 波 3；HEAD 见 CURRENT-HANDOFF） |
| 上游事实源 | A NFR8（多租户预留）· D §12 开放问题 #1/#3 · B §8（repo_path 校验路径）· M7-HANDOFF §8 挂账 + CURRENT-HANDOFF §5 挂账 · CURRENT-HANDOFF 更新行「⚠ 待 owner 决 → 已收」（D v1.0.5 boot_nonce/对账 #9） |
| 定位 | MVP 的三条信任基座——**单工作区 / 单机 / 单进程**——各自把一部分正确性押在「就一个」这个前提上。本文逐条盘点这些前提的**边界**（哪些表/引用/进程内状态依赖它）、**为什么 MVP 内安全**、**什么会打破它**、**归哪个 M-里程碑消费**。**发现均为设计内预留或已知残余窗口，非缺陷**；实施一律 M8+，本里程碑只登记挂账。 |
| 读法 | §1–§4 = 三信任基座逐面盘点；§5 = 本里程碑 M7b 新登记的五个残余窗口（我方复审确认）；§6 = 挂账登记总表（owner 过目落点）。 |

---

## 0. 三条信任基座（一句话）

| 基座 | MVP 前提 | 兑现方式（代码事实） | 打破者 |
| --- | --- | --- | --- |
| **单工作区** | 系统内恰一个 workspace 行 | `deps.workspace_row` = `SELECT * FROM workspaces LIMIT 1`（deps.py:112）——**无 workspace 路由**，任何请求都落到"那一个"工作区 | 第二个 workspace 出现（多租户 / Joint Channel） |
| **单机** | 恰一个 computer，所有 Project 同机 | `projects.computer_id` FK 已建但 MVP 只有一台；daemon 与 server 同机同根（D §9.1） | 第二台 computer 接入（跨机 Project / 跨机预览） |
| **单进程** | server 恰一个进程、daemon 恰一个进程 | 进程内锁 `_landing_lock`、进程内去重游标 `_deploy_log_seq`、进程内 nonce 表 `_last_boot_nonce`（hub.py） | server 水平扩容多副本 / daemon 多实例 |

---

## 1. workspace_id 全实体覆盖面（A NFR8）

**盘点方法**：`grep -nE "__tablename__|workspace_id" db/models.py` 逐表核对是否直挂 `workspace_id` 列。

### 1.1 直挂 workspace_id 的实体（20 张，多租户就绪）

`computers` · `members` · `channels` · `messages` · `files` · `reminders` · `diagnostic_events` · `token_usage_events` · `landing_batches` · `canvases` · `tasks` · `activity_items` · `task_contracts` · `held_drafts` · `templates` · `proposals` · `projects` · `worktrees` · `preview_sessions` · **`deployments`（M7b 新增，models.py:892）**。

> **M7b 结论**：本里程碑新建的 `deployments` 表**已直挂 `workspace_id` FK**（POST 建行时从 project 快照落列），与 preview_sessions（M7a）同口径。**M7 未在多租户覆盖面上留新债**——两张新表都进了「就绪」集。

### 1.2 不直挂 workspace_id 的实体（13 张）——三类，均安全

| 类别 | 表 | 租户归属推导路径 | MVP 安全性 |
| --- | --- | --- | --- |
| **A. 全局字典** | `agent_role_templates` | **无租户**（models.py:230 注释「全局字典表，无 workspace_id」）——Orchestrator 内置角色是跨工作区共享的字典 | 设计内无租户；多租户化后仍全局共享，**无需改** |
| **B. 子表（经 PK 父引用继承租户）** | `agents`（→members/computers）· `agent_skills`（→agents）· `task_events`（→tasks）· `canvas_nodes`（→canvases）· `canvas_edges`（→canvases）· `ledger_entries`（→landing_batches.batch_id） | 无独立 workspace_id，租户由父行携带；查询恒经父行下钻 | 单工作区内父行租户唯一，推导无歧义 |
| **C. 结点/关联表（复合 PK 双引用）** | `channel_members`（channel+member）· `message_mentions`（message+member）· `read_positions`（member+channel）· `message_task_refs`（message+task）· `channel_notification_settings`（channel+member）· `channel_projects`（channel+project） | 两端 FK 均须同工作区，但**无 DB 级同租户约束** | 单工作区内两端天然同租户；见 §2 破坏面 |

### 1.3 什么会打破 / 归属

- **打破者**：第二个 workspace 出现后，§1.2-C 的关联表两端可能跨工作区（如 channel 属 WS-A、member 属 WS-B），DB 无约束拦截；§1.2-B 子表查询若不经父行下钻直接全表扫，会跨租户泄漏。
- **当前防线**：`deps.workspace_row` 单工作区解析（LIMIT 1）使「跨租户」在 MVP 物理上不可达——只有一个租户，无从跨。
- **归属**：**M8+ 多租户批**。届时须：① 关联表加同租户 CHECK 或应用层校验；② 所有读端点强制 `WHERE workspace_id = <当前租户>`（现多数端点已按 `ws["id"]` 过滤，见 routes/channels.py:83 等，但 `workspace_row` 本身是 LIMIT 1，须换成请求携带的租户身份）。

---

## 2. 频道跨工作区引用位（Joint Channel 预留）

**背景**：`channels.joint_ref`（models.py:266，`Text nullable`）是 PRD 预留的 **Joint Channel**（跨工作区协作频道）唯一落点；MVP 恒 NULL、无任何读写消费（grep 全仓仅模型定义一处命中，无业务逻辑引用）。

### 2.1 跨工作区引用点清单（单工作区内安全，多工作区内需校验）

| 引用位 | 表/列 | 跨租户风险（多工作区时） | MVP 安全性 |
| --- | --- | --- | --- |
| 频道成员 | `channel_members(channel_id, member_id)` | 外部工作区成员加入本频道 = 无同租户校验 | 单 WS 内两端同租户 |
| 消息作者 | `messages.author_member_id` | 外部成员在本频道发言 | 同上 |
| @提及 | `message_mentions(message_id, member_id)` | 提及外部成员 | 同上 |
| 任务归属 | `tasks.owner_member_id` / `created_by_member_id` | 外部成员认领/建本频道任务 | 同上 |
| 已读位 | `read_positions(member_id, channel_id)` | 外部成员的已读游标 | 同上 |
| 频道-Project 绑定 | `channel_projects(channel_id, project_id)` | 频道绑定外部工作区 Project → 触发跨租户交付/部署 | 单 WS 内 project 与 channel 同租户 |
| 频道通知设置 | `channel_notification_settings(channel_id, member_id)` | 外部成员的通知偏好 | 同上 |

### 2.2 什么会打破 / 归属

- **打破者**：`joint_ref` 被赋值、频道对第二工作区开放成员后，§2.1 所有引用位都可能跨租户，且**投递/唤醒/gating 判定**（hub 投递语义 §8）会把外部成员纳入投递对象——注入面（§4.3）随之放大。
- **归属**：**M8+ 多租户 / Joint Channel 批**（M7-HANDOFF §8「多租户/Joint Channel/多人类实施」非目标 #2/#11——本里程碑只审查预留位）。届时须定义「频道租户 vs 成员租户」的合法交集规则，并把投递对象判定收敛到该交集。

---

## 3. 多机预留三件

MVP「单机」= 恰一台 computer、Project 全同机、daemon 与 server 同根共居（D §9.1）。三处已埋跨机接缝但未实施：

### 3.1 `projects.computer_id` 路由（D §12 #1，已收回但只单机验证）

- **预留/假设**：`projects.computer_id` FK NOT NULL（models.py:766）已建——交付类指令（worktree/preview/deploy）**按该列路由到宿主 daemon**（D §12 #1「按该列路由到宿主 daemon；MVP 单机恰一 computer」）。M7b 的 `request_deploy_run(computer_id=...)` 已按 `project.computer_id` 取连接下发（hub 侧 `self._conns.get(computer_id)`）。
- **MVP 安全**：只有一台 computer、一条 daemon 连接，`computer_id` 路由恒命中那一条；离线即 503。
- **打破者**：第二台 computer 接入后，路由正确性依赖 `computer_id` 列的准确性 + 每台 daemon 只认自己 Project 的指令；跨机对账（对账 #9/#10 的 boot_nonce 判据是 per-computer 的，已 per-computer 键化 `_last_boot_nonce[computer_id]`，此处已就绪）。
- **归属**：多机批（路由基本就绪，主要缺跨机验证与故障隔离）。

### 3.2 D §12 #3 端口代理（跨机预览，FR-11.4 预留）

- **预留/假设**：`preview.status` 已带 `port` 字段（D §7），iframe 直连 `http://127.0.0.1:{port}`——**假设 dev server 与浏览器同机可达**。D §12 #3 明写「MVP 本机不做，届时加代理层不动协议」。
- **MVP 安全**：单机——浏览器、server、daemon、dev server 全在 `127.0.0.1`，端口直连可达。
- **打破者**：Project 在远程 computer 上，dev server 端口对浏览器不可达——需端口代理层（server 反代到宿主机端口）。
- **归属**：多机批（M7-HANDOFF §8「跨机预览端口代理实施（D §12 #3 预留已带 port）」→ 多机批）。**协议不变**（port 字段已在）。

### 3.3 B §8 #4 repo_path 校验路径（server 直读 fs vs daemon 查询帧）

- **预留/假设**：POST /deployments 触发时，**server 侧直接读文件系统解析主干 HEAD**（`_resolve_head(project["repo_path"])`，deployments.py:150/226；GitPython/子进程 `git -C repo_path rev-parse`）——**假设 server 与 repo 同机**（先例 = M7a preview 的 repo_path 校验路径、D §12.12 #1）。同理 preview 的 worktree_path 也走同机假设。
- **MVP 安全**：单机——`repo_path` 是本机绝对路径，server 直读命中。解析失败 → 结构化 4xx（`_head_resolve_error`，details field=`repo_path` + hint）。
- **打破者**：repo 在远程 computer 上，server 无法直读其文件系统——须改由 **daemon 查询帧**（如 `git.diff` 的 query 体例，D §6）代取 HEAD，即「server direct fs → daemon query frame」的迁移。
- **归属**：多机批。当前 server 直读是单机捷径，跨机须换成 daemon query（协议已有 query kind 骨架，加一条 `git.head` query 即可，不改帧模型）。

---

## 4. 单进程假设清单

MVP「单进程」= server 恰一副本、daemon 恰一实例。以下进程内状态/锁把正确性押在「同一进程」上：

### 4.1 `_landing_lock` 跨进程双直落批（M6b 审计登记项）

- **预留/假设**：J9 落地扫描用**进程内** `asyncio.Lock`（`self._landing_lock`，hub.py:304，注释「跨进程由账本三态兜」）防重入；`landing_batches` 表**无 `(kind, source_ref)` 唯一约束**（models.py:417–429 确认无该唯一索引）——单进程内 `_landing_lock` 串行化保证同一 source_ref 不并发建双批。
- **MVP 安全**：单 server 进程——`_landing_lock` 串行化所有落地扫描；账本三态（running/`:done`/fail-closed）+ `ledger_entries.op_id` 唯一（uq_ledger_op_id）在跨进程面兜底幂等，但**建批本身**的去重仍靠进程内锁。
- **打破者**：server 多副本——两副本可同时通过 `_landing_lock`（各进程各一把锁），对同一 `(kind, source_ref)` 建两条 running 批。账本 op_id 唯一能挡住重复 op 落地，但两条 batch 行本身会并存（display/对账面重复）。
- **归属**：**M8+ 多机/多副本批**（M7-HANDOFF §8「跨进程双直落批 → 实施归 M7 后多机批」；CURRENT-HANDOFF §5「landing_batches 无 (kind,source_ref) 唯一约束——M7 多机化时收」）。收法 = 加 `(kind, source_ref)` 部分唯一索引（活动批），把去重从进程锁下沉到 DB。

### 4.2 reconnect / boot-nonce（D v1.0.5，per-computer 进程内 nonce 表）

- **预留/假设**：对账 #9/#10 用 `boot_nonce` 区分「同进程 WS jitter」与「真 daemon 重启」（hello 携 nonce，hub.py:511–517）；server 侧存 **进程内** `_last_boot_nonce: dict[computer_id, nonce]`（hub.py:317）。`daemon_restarted` 判据 = `nonce is None or prev is None or prev != nonce`（缺省 True = 旧 daemon 无 nonce 按全量 fail-close，向后兼容）。
- **MVP 安全**：单 server 进程——`_last_boot_nonce` 是该进程的连续观察；**server 自身重启后该表清空** → `prev_nonce is None` → `daemon_restarted=True` → 保守全量 fail-close（正确的悲观兜底，见 hub.py:315–317 注释）。
- **打破者**：server 多副本——daemon 重连可能落到**不同副本**，该副本 `_last_boot_nonce` 无此 computer 的历史 → 每次都判「重启」→ 存活预览被误 fail-close（jitter-survive 失效）。跨副本须把 nonce 表移到共享存储（DB 列 `computers.last_boot_nonce`）。
- **归属**：多副本批。**单进程内完全正确**（M7a jitter-survive 14/14 + code-review 收口已证）。

### 4.3 注入面 prompt 中和（单工作区信任模型内；M6b 审计登记项）

- **预留/假设**：线程摘要 verbatim 进 Orchestrator 注入体，可被伪 `[system` 前缀操纵（prompt injection）——**单工作区信任模型内**所有成员可信（同一 owner 的工作区），direct 频道无外部人类闸放大。
- **MVP 安全**：单工作区 = 单一信任域；所有 Agent/人类同属一个 owner，无外部不可信输入源。
- **打破者**：多用户 / Joint Channel（§2）引入外部不可信成员，其消息进入注入体即可操纵 Orchestrator。
- **归属**：观察项（M7-HANDOFF §8「注入面 prompt 中和 → 观察项（多用户化前收）」；CURRENT-HANDOFF §5 同）。多用户化前须做输入中和（分隔/转义/结构化边界）。

### 4.4 `_deploy_log_seq` 去重游标（M7b，本里程碑新登记——详见 §5a）

- 进程内 `dict[deployment_id, max chunk_seq]`（hub.py:314）——见 §5(a) 残余窗口。

---

## 5. 本里程碑 M7b 新登记的残余窗口（我方复审确认）

以下五项为块 M7b 实现 + 复审中确认的**已知残余窗口**，均在 MVP 单机/单进程信任模型内安全或影响仅限展示面，登记挂账 M8+ 消费。

### (a) K4 deploy.log 落盘去重游标仅内存（display-only）

- **窗口**：deploy.log 落盘去重游标是**内存** `self._deploy_log_seq: dict[str,int]`（hub.py:314，收终态后 pop）。若 server 崩溃于「日志行已落盘 commit 后、ack 送达 daemon 前」再重启，daemon 重连重传未 ack 的 deploy.log 帧 → 重启后的 server 内存游标已丢（从空重建）→ 该帧被当新帧再次追加 → **磁盘日志文件重复行**。
- **为何 MVP 安全**：纯**展示面**（日志文件仅供 GET /deployments/{id}/log 翻页展示）；**状态 CAS 不受影响**（deployment 状态推进走条件 UPDATE，与日志去重游标无关）。deploy.finished 的终态落库仍幂等（条件 UPDATE `WHERE status IN (queued,running)`，重复 noop）。
- **打破者**：上述精确崩溃时序（落盘后 / ack 前 / server 重启）。
- **归属**：**M8+**——去重游标持久化（落 `<id>.log` 旁 `.seq` sidecar 或从文件行数恢复），把去重从进程内存下沉到磁盘。

### (b) K4 对账 #10 queued 安全重发残窗（契约授权的设计固有窗口，副作用重放）

- **窗口**：daemon 起子进程产出输出，但**首条 deploy.log 未达 server 前** daemon 硬崩溃重启 → server 侧行仍 `queued`（未见任何 running 信号）→ 对账 #10 判「queued 未 ack」→ **重发 deploy.run** → 部署命令**二次执行**（外部副作用重放）。
- **为何 MVP 安全 / 定性**：**属契约 D §4.4 #10 明确授权的「queued 可安全重发」设计**（「queued → 重发 deploy.run；daemon 已在跑/终态 noop」）——**非实现偏差**。真正的不可重放保护落在 `running` 分支（running 失进程 = fail-closed 置 failed @触发者，绝不重跑）；queued 分支的重发窗口是「daemon 在 server 收到任何 running 证据之前就崩溃」这一极窄时序，契约选择接受它换取 queued 补发的简单性。
- **打破者**：daemon 在「子进程已起跑」到「首条 deploy.log 到达 server」之间硬崩溃。
- **归属**：**M8+**——收窄需 daemon 侧持久化「已起跑」标记（起子进程前先落盘 fail-closed 标记，重连时据此判 queued 是否真未跑），把 queued 的可重放边界从「未见 running 帧」收紧到「未落起跑标记」。

### (c) K4 硬崩溃孤儿 dev server / deploy 子进程泄漏（K2-cal §3.4 登记，MVP 接受）

- **窗口**：win32 asyncio 子进程**不随父进程死**（K2-cal `PREVIEW-CALIBRATION.md` §3.4 实测登记）。daemon 硬崩溃（非优雅退出，未走 `_kill_process_tree`）→ 已起的 dev server / deploy 子进程成**孤儿**，继续占端口/占资源直至手动清理或系统重启。
- **为何 MVP 安全**：优雅退出路径（shutdown / cancel）已 `taskkill /F /T` 杀树无孤儿（M7a verify 孤儿 0 已证）；仅**硬崩溃**（kill -9 / 断电）路径泄漏。daemon 重连后对账 #9 的「反向泄漏防护」能回收 server 已知的存活预览（下发 preview.stop），但对**孤儿**（daemon 进程都没了、新 daemon 无该子进程句柄）无能为力——新 daemon nonce 变化 → 对账把 DB 活跃行 fail-close，但 OS 层孤儿进程仍在。
- **打破者**：daemon 硬崩溃 / 断电。
- **归属**：**M8+**（MVP 接受）——需 OS 层孤儿清扫（启动时按端口/进程名扫残留 coagentia 子进程并杀），或子进程 job object（win32）绑定父进程生命周期。

### (d) B-M7-2 部署确认弹窗触发者展示口径（单人 MVP 无实际错误）

- **窗口**：前端部署确认弹窗把「触发者」展示为**频道 owner 名**而非当前 acting member。
- **为何 MVP 安全**：单人 MVP 下 owner = 用户本人，展示无实际错误。**服务端落准**——deployments.py:205/268 `me = acting_member(request, tx.conn)` → `triggered_by_member_id=me["id"]`，行内 triggered_by 是真实触发主体（人类 owner 或经 X-Acting-Member 的 Agent），结果卡 mention 也用 `row["triggered_by_member_id"]`（hub.py:1032）。仅**前端弹窗预填文案**取了 owner 名。
- **打破者**：多人类成员 / Agent 触发时，弹窗显示的触发者名与实际不符（展示误导，非数据错误）。
- **归属**：**多人化前收**（前端小修：弹窗触发者取当前 acting member 而非频道 owner）。

### (e) B-M7-2 部署日志「重开仍在跑的部署」快照-实时流交叠边缘态（MVP 观察项）

- **窗口**：重新打开一个**仍在运行**的部署的日志视图时，首页历史 `GET /deployments/{id}/log?after=` 与 `deployment.log` WS 订阅流并发到达，两者游标未对齐 → 交叠区可能**乱序 / 丢帧**（历史请求截止点与订阅起始点之间的行）。
- **为何 MVP 安全**：**主路径正确**——建时开卡（空历史 + 纯实时流）、终态部署（有历史 + 无实时流）两条主路径无交叠，游标对齐无歧义。仅「重开一个进行中部署」这一边缘态触发历史/实时竞争。
- **打破者**：用户在部署 running 期间关闭再重开日志视图。
- **归属**：**M8+**（MVP 观察项）——需游标对齐协议（历史 GET 返回截止 chunk_seq，订阅从该 seq+1 起接，或订阅先缓冲再拼接历史），把重开边缘态收敛到主路径同等确定性。

### (f) 对账 #10 fail-close 与 daemon 缓冲 deploy.finished=success 的竞争（fail-closed 保守，非损坏）

- **窗口**：部署命令已执行**成功**、daemon 已把 `deploy.finished(success)` 落缓冲（`deploy-finished.jsonl` 持久，需 ack）但**发送前硬崩溃** → 重启（新 boot_nonce）→ reconnect 握手先跑对账 #10，把该 running 部署 fail-close 为 `failed`(exit_code=NULL)；随后 daemon 重连重传缓冲的 `deploy.finished(success)`，其终态 CAS `WHERE status IN (queued,running)` 因行已 `failed` → rowcount=0 丢弃。结果：**实际成功的部署被记为 failed「结果未知」**。
- **为何 MVP 安全（其实是"按设计保守"）**：这正是铁律 3「副作用不可重放 → 部署命令跑一半 daemon 死则 fail-closed @人工核实」的**保守兑现**——把"结果未知"一律当失败、由人核实。此窗口下副作用（部署命令的真实外部效果）**未改变**（命令确实成功执行过），仅**记录态偏悲观**；人工核实即发现其实成功。不产生数据损坏、不重放副作用。
- **打破者**：daemon 于「deploy.finished 已缓冲、未发送」瞬间硬崩溃 + 重启。
- **归属**：**M8+**——若要honor 已知结果：reconnect 时**先处理 daemon 缓冲上报（deploy.finished）再跑对账 #10 fail-close**（缓冲带真实终态则不 fail-close），或允许 `failed(exit_code=NULL, 由 reconcile 置)` 被随后到达的真实 deploy.finished 覆盖（须谨慎破除终态不可变）。当前保守口径与铁律 3 一致，MVP 接受。

---

> **收口修复批（/code-review high，2026-07-13）**：M7 收口复审（8 维 finder→对抗核实，12 CONFIRMED）中，**4 项真实缺陷已在本里程碑内修复**（非 M8+ 挂账）：① `GET /deployments/{id}/log` 无分页上限却对任何非空日志返 `next_after=total` → 前端误显"加载更多"空按钮 → 改 `next_after=None`；② `_report_deploy_log` 内存去重游标先于 gateway_tx 提交推进 + 落盘在事务内 → 回滚后 daemon 重发被误吞（丢帧）/ 或重复落盘 → 改**落盘+游标推进挪到提交后**（回滚则干净重处理，不吞不重复）；③ `GET /usage?level=agent` 的 `usage` 按 `agent_member_id` 聚合而覆盖率/明细按 owner 任务集 → 二者互不一致 → 改 usage 同源 owner 任务集（`usage == Σ breakdown`）；④ `_append_deploy_diagnostic` 与 `_append_preview_diagnostic` ~40 行重复 → 抽 `_emit_agent_diagnostic` 单点。修复 ② 顺带**收窄 R-10**：回滚导致的重复行/丢帧变体已闭合，R-10 仅余「server 崩溃于提交后-ack 前 + daemon 重传」这一 display-only 残窗（游标仍需持久化才彻底闭合）。

## 6. 挂账登记表（owner 过目落点；M8+ 消费）

| # | 项 | 出处 | 破坏者 | 归属里程碑 | 收法（建议） |
| --- | --- | --- | --- | --- | --- |
| R-1 | §1.2-C 关联表无同租户约束（channel_members/message_mentions/read_positions/message_task_refs/channel_notification_settings/channel_projects） | A NFR8 / §1.2 | 第二个 workspace | M8+ 多租户批 | 关联表同租户 CHECK 或应用层校验 |
| R-2 | 读端点租户身份仍 `workspace_row` LIMIT 1（无请求级租户路由） | deps.py:112 / §1.3 | 多工作区 | M8+ 多租户批 | 请求携租户身份，端点强制 `WHERE workspace_id=<租户>` |
| R-3 | `channels.joint_ref` 跨工作区频道引用位（MVP 恒 NULL 无消费） | §2 / 非目标 #2/#11 | Joint Channel 启用 | M8+ Joint Channel 批 | 定义频道租户 vs 成员租户合法交集 + 投递对象收敛 |
| R-4 | `projects.computer_id` 跨机路由（基本就绪，缺跨机验证/故障隔离） | D §12 #1 / §3.1 | 第二台 computer | 多机批 | 跨机路由验证 + 故障隔离 |
| R-5 | 跨机预览端口代理（port 字段已预留，协议不变） | D §12 #3 / §3.2 | 远程 Project dev server | 多机批 | server 反代到宿主机端口的代理层 |
| R-6 | repo_path 校验 server 直读 fs（单机捷径） | B §8 / D §12.12 #1 / §3.3 | 远程 repo | 多机批 | 换 daemon query 帧（加 `git.head` query，不改帧模型） |
| R-7 | `_landing_lock` 跨进程双直落批（landing_batches 无 (kind,source_ref) 唯一） | M6b 审计 / §4.1 | server 多副本 | M8+ 多机/多副本批 | 加 (kind,source_ref) 活动部分唯一索引 |
| R-8 | boot_nonce 表 `_last_boot_nonce` 进程内（多副本失 jitter-survive） | D v1.0.5 / §4.2 | server 多副本 | M8+ 多副本批 | nonce 下沉 `computers.last_boot_nonce` 列 |
| R-9 | 注入面 prompt 中和（单工作区信任域内） | M6b 审计 / §4.3 | 多用户 / Joint Channel | 观察项（多用户化前收） | 注入体输入中和（结构化边界/转义） |
| R-10 | deploy.log 去重游标仅内存（崩溃后磁盘日志重复行，display-only） | §5(a) | server 崩溃于落盘后/ack 前 | M8+ | 去重游标持久化（.seq sidecar / 文件行数恢复） |
| R-11 | 对账 #10 queued 安全重发残窗（副作用重放；**契约授权的设计固有窗口**） | D §4.4 #10 / §5(b) | daemon 崩溃于起跑后/首条 log 前 | M8+ | daemon 侧持久「已起跑」标记 fail-closed 收窄 |
| R-12 | 硬崩溃孤儿 dev server/deploy 子进程泄漏（win32 子进程不随父死） | K2-cal §3.4 / §5(c) | daemon 硬崩溃/断电 | M8+（MVP 接受） | OS 层孤儿清扫 / win32 job object |
| R-13 | 部署确认弹窗触发者展示为频道 owner 而非 acting member（server 落准，仅前端文案） | §5(d) | 多人类/Agent 触发 | 多人化前收 | 前端弹窗取当前 acting member |
| R-14 | 部署日志重开进行中部署的快照-实时流交叠（主路径正确） | §5(e) | running 期关闭再重开日志 | M8+（观察项） | 游标对齐协议（历史返截止 seq，订阅从 seq+1 接） |
| R-15 | 对账 #10 fail-close 与 daemon 缓冲 deploy.finished=success 竞争（记为 failed 结果未知，副作用未改，铁律 3 保守兑现） | §5(f) | daemon 于 finished 已缓冲未发送时硬崩溃 | M8+ | reconnect 先处理缓冲上报再跑 fail-close / 允许真实终态覆盖 reconcile 置的 failed |

> **总结**：M7b 未在多租户覆盖面留新债（deployments 表已直挂 workspace_id）。残余窗口 R-10~R-15 均为 display-only、契约授权的设计固有窗口、MVP 明确接受的 win32 硬崩溃泄漏、或铁律 3 保守 fail-closed 的悲观记录——**无一影响状态机正确性或造成数据损坏**（部署状态推进全程条件 UPDATE CAS + 副作用不可重放 fail-closed 双保险）。收口复审的 4 项真实缺陷（next_after / deploy.log 提交后落盘 / usage 一致性 / 诊断去重）已**在里程碑内修复**（见上方收口修复批），R-10 rollback 变体随之闭合。三条信任基座（单工作区/单机/单进程）依赖点已逐一定位，M8+ 消费路径清晰。**本文档零代码变更，交 owner 过目。**
