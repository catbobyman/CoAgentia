# B-1 投递前缀死锁 — 修复方向利弊分析与裁决

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-15，owner 授权自主裁决；问题实证 = [M8-REALTEST-FINDINGS.md](verify/M8-REALTEST-FINDINGS.md) B-1（`437eb30`） |
| 问题 | 串行链拆解落地后，blocked 任务锚点被投递 gating 扣住 + 「连续前缀」规则首个被扣即截断 → **全频道 Agent 投递永久楔死**（含「已落地」@唤醒）；且任何人在 blocked 任务线程发言同样触发。真机 3 Agent 全饿死实证、绕过后恢复实证 |
| 裁决结论 | **方向 ②′：gating 改「跳过不截断」（唤醒抑制保留、blocked 内容仍不投）＋ 解锁主动唤醒消息（@suggested_owner，节点级幂等）**。零契约帧变更；同时修复「解锁无唤醒」姊妹缺口 |

## 1. 事实底座（裁决依据，全部本轮新核实）

| # | 事实 | 出处 | 对裁决的意义 |
| --- | --- | --- | --- |
| F1 | **契约 D 层 gating 只绑唤醒面**：§4.4 原文「画布 gating（C3：blocked 任务的 owner 不因**该任务被唤醒**）」「gating 否决权最高：任务触发类**唤醒**在节点 blocked 时不发（人类 force-start 后 server 补发同款 wake）」 | [04-daemon-server协议.md:162,173](../../engineering_docs/04-daemon-server协议.md) | 「扣投递（含 busy 直投）+ 前缀截断」是 M3b **实现层裁决 2 的扩界**，不是契约义务。收窄投递面不动契约帧 |
| F2 | **锚点系统消息无 mention 行**（实测 N2 锚点 `mentions=[]`） | 真机库查证 | 现设计里解锁后即使锚点经对账重投也**不唤醒任何人**——#7「不跳过、解锁后随对账投递」换来的只是上下文推送，唤醒面本就缺失（姊妹缺口：**解锁无唤醒**） |
| F3 | **claim 无 blocked 门**（claim 端点只查终态与 owner 空，[tasks.py:131](../apps/server/src/coagentia_server/routes/tasks.py:131)）；Agent 经 list_tasks/get_task 可见全部任务 | 代码核对 | 「扣投递防抢跑」是**软屏障**（拉取面全开）；防抢跑的硬门实际在 worktree 层（ensure 排除 blocked / delivery_waits fail-closed）与系统节点抑制 |
| F4 | **前缀连续性的硬约束**：daemon 按频道**最大 message_id 去重**（契约 D read_position/watermark），越过被扣消息先投后者，解锁后早消息重投必被 noop 永久漏投（[hub.py:1543-1545](../apps/server/src/coagentia_server/computers/hub.py:1543) #7 权衡原文） | 代码注释 | 「不跳过」有真实理由——**前提是被扣消息日后还需要投**。若被扣消息的价值可由其他面补偿（F2+F5），跳过即合法 |
| F5 | **投递是推送优化，非事实源**：Agent 开工惯例经 get_thread/get_messages 拉全线程历史（真机实测 claude-A 每任务均先拉线程） | 真机观察 | 被扣的 blocked 线程历史消息不推送也不丢信息——Agent 接手任务时自然拉到 |

## 2. 三方向利弊

### 方向 ①：落地消息序调整（「已落地」先于 blocked 锚点）——否决

- **构想**：让唤醒载体先于会被 gate 的锚点入队。
- **致命伤**：锚点 = 任务的 `root_message_id`，**锚点先于任务存在是落地步进的结构性事实**（每步=节点+锚点一个事务，`:done` 恰一次在末尾发「已落地」）——想把「已落地」提前到锚点之前等于宣布尚不存在的任务已落地，事务结构与语义双重不允许。
- **且不解通例**：blocked 线程后续任何发言（人类评论、系统提醒）仍楔死全频道。修了一个窗口，留下整面墙。

### 方向 ③：契约 D 逐消息去重（deliver 帧携 per-message 去重 + 被扣消息解锁重投）——否决

- **构想**：daemon 按消息 id 去重取代高水位，被扣消息不推水位、解锁后精确重投。
- **优点**：机制上最"完备"，被扣消息一条不丢。
- **致命伤**：契约 D 升版 + daemon read_position/ack 全链改造，与 M7a/M8a 反复打磨的 ack/watermark 并发语义（L4a 三不变量：帧序/ack/emit）正面相撞，回归半径全项目最大；还需发明「解锁→重投队列」新机制。而它换来的能力——重投 blocked 线程历史——按 F5 价值很低（Agent 拉取补齐），按 F2 连唤醒都不带。**最高的成本买最低的收益**。

### 方向 ②（原案）：gating 收窄为只抑制唤醒、照常投递——部分否决，修正为 ②′

- **构想**：gated 消息照常进投递批，仅不构成唤醒理由。前缀永不截断，死锁根除，零契约变更。
- **问题**：违背 M3b 裁决 2 的教训本体——blocked 任务内容被 busy 直投进 Agent 实时会话 / 随他由唤醒进批，Agent 可能提前开工（F3 说明拉取面本就开放，但**推送进会话的诱导强度远高于被动可查**；M3b 当年正是吃了这个亏才扩到投递双面）。直接回退教训不可取。

### 方向 ②′（裁决采纳）：gating 改「跳过不截断」＋「解锁主动唤醒」

**两刀合为一个语义闭环**：

**第一刀——跳过不截断**：`_filter_agent_delivery` 遇 gated 消息**跳过该条**（不入批），继续处理后续消息，水位照常推进到批内最大 id。效果：
- blocked 内容**仍然不投**（M3b 防抢跑教训完整保留，比方向②强）；
- 前缀不再截断 → 死锁面根除（落地场景 + blocked 线程发言通例 + O8 blocked_at 同函数受益）；
- 代价 = 被跳过的消息永不推送（水位已过）。由 F5（拉取补齐）+ 第二刀（解锁唤醒）补偿。

**第二刀——解锁主动唤醒**：blocked→unblocked 翻转点（gating 推进处）为该节点发一条**任务线程系统消息 @suggested_owner**（带 mention 行；节点级幂等恰一次；无 suggested_owner 则不 @ 只留痕）。效果：
- 修复 F2 揭示的「解锁无唤醒」缺口——这是**现状就存在的产品缺陷**（真机实测 N1 done 后无人唤醒 N2 建议人，靠人工 @ 才动）；
- 为第一刀跳过的锚点提供**更强的替代唤醒载体**（system+mention = REMINDER 触发，比原锚点无 mention 的静默重投强一级）；
- 与契约 D §4.4「force-start 后 server 补发同款 wake」的既有恢复哲学同构。

**为什么这是唯一同时满足四条约束的方案**：不动 watermark 语义（F4 硬约束）、不回退 M3b 教训（blocked 内容不投）、修死锁通例（不截断）、补齐解锁唤醒（F2）。

## 3. 影响面核对

| 面 | 结论 |
| --- | --- |
| 契约 D | **零帧变更**（wake/deliver/ack 形状与语义不动）。gating 的契约文本（§4.4）本就只绑唤醒面（F1），投递扣留是实现层裁决——本裁决把实现拉回契约字面 + 保留教训内核。D §4.4 可加一行备注「blocked 消息不投递=跳过语义，解锁另发唤醒消息」，属澄清非变更（D 小版可选） |
| 契约 A/B/C/E/E2 | 零变更。解锁消息 = 既有 post_system_message + message_mentions（零新表零新事件零新帧） |
| M3b 裁决 2 | **升级记录**：「gating 作用于唤醒+投递双面」→「唤醒抑制 + 投递跳过（不截断）+ 解锁唤醒」；CURRENT-HANDOFF §8 注意事项该行同步改写 |
| M6 #7 前缀权衡 | 截断理由（防漏投）对 gating 面不再成立（跳过+解锁唤醒补偿）；**对 worktree `delivery_waits_for_directory` 截断保留**——其被扣消息（canvas_activation 载体）无替代唤醒面，且触发前提（ensure 失败）已有诊断+三次升级兜底。登记为同族观察项，不在本批动 |
| O8（M8b） | `is_summary_blocked` 同函数受益（汇总线程消息不再截断他人前缀）；stall/note_wakeup 挂唤醒面不挂投递面，跳过语义无扰。三红例回归守门 |
| 前端 | 零改动（解锁消息按普通系统消息渲染） |

## 4. 实施草图

1. **`hub._filter_agent_delivery`**：gated → `continue`（跳过该条，不再 `hit_held=True` 截断）；`delivery_waits_for_directory` 分支维持截断不动。水位取批内实际投出的最大 id——**注意**：若批尾连续 gated，水位止于最后实投消息（不越过未投尾巴，防「尾巴解锁后被水位吞」；尾巴消息将由后续消息投递时重评或解锁唤醒兜底）。
2. **`hub._deliver_message`**：入口的 `message_delivery_gated(msg)` 短路改为「本消息 gated → 不作为唤醒理由、不因它起投递，但不 return（仍走前缀冲洗判定）」——保证 busy 冲洗与既有前缀里非 gated 消息照常流动。
3. **解锁唤醒**：gating 推进点（derive_blocked 翻转消费处，与 force-start 补发 wake 同层）新增 `_notify_node_unblocked`：任务线程 system 消息 + mention suggested_owner；幂等键 = 节点 id（diagnostic 或消息存在性判定，沿 L11 问候幂等标记先例）。
4. **测试**（守门）：
   - 新增回归 = 复刻 realtest 串行链：落地后 3 Agent read_position 均可推进、「已落地」@建议人可达（死锁不再）；
   - 既有 gating 测试改断言：blocked 线程消息**不在投递批**（跳过）且**不触发唤醒**（双断言取代「截断」断言）；
   - 解锁唤醒：N1 done → N2 线程出现 @建议人系统消息恰一次（重复 done/reopen 不重发）；
   - O8 三红例 + force-start 补发 wake 回归；全量 pytest 只增不减。
5. **文档**：CURRENT-HANDOFF §8 gating 行改写 + 本文档挂链；D §4.4 备注行（可选小版）。

## 5. 开放问题（登记不阻塞）

- worktree `delivery_waits_for_directory` 截断的同族饿死面（触发前提 = ensure 失败长期悬挂，如 project 绑死机器——真机已实测发生）：本批不动，依赖既有诊断+升级兜底；若 B-1 修后实测仍见楔死，再立案。
- 批尾连续 gated 的水位语义（草图 #1 注意项）需在实现时写探针钉住。
- 解锁唤醒对**并行多节点同时解锁**的合并策略（逐节点各一条 vs 合并一条）：先逐节点（简单+幂等清晰），噪音实测后再议。
