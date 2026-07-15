# M8 上线前实机测试方案（编排能力专项）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-14，owner 提出「设计高复杂度任务测编排能力 + Codex 节点稳定性 + 评审编排 + 多频道多项目并行 + 非 coding 任务（deep research 长报告）」；本文为场景设计，**待 owner 审核后执行** |
| 定位 | M8c L13 `m8_verify` 的**真机场景输入**（对应 [M8-HANDOFF](../project-handoffs/M8-HANDOFF.md) §9b #11、§9c #13–#16）；与探针脚本互补——本文测「真 CLI + 真人操作」路径，探针脚本测可控红例 |
| 范围外 | 多租户/多机/多副本（R-1~R-9，M9+ 挂账）；部署适配器（Vercel/Netlify，P2）；性能基准测量（只观察不设指标线） |
| 执行原则 | ① 真 token 只烧在「非真 CLI 不可」的面上，可控红例一律 daemon-sim；② **边测边截图**——checklist 每项勾销当场截图，不事后补拍（详见 §3）；③ 每任务出证据（截图 + 消息链接 + 结论行）归档 [M8-EVIDENCE.md](M8-EVIDENCE.md)；④ 实机进程结束前必杀（taskkill /F /T） |

---

## 0. 测试环境与前置条件

### 0.1 启动形态

- **服务**：终端 1 `uv run coagentia-server`（8787）+ 终端 2 `pnpm --filter @coagentia/web dev`（5173）；浏览器走 5173（代理 /api→8787）。
- **daemon**：真 daemon（非 sim）接真 CLI；仅 T-D 红例分支与 T-E 护栏红例改用 daemon-sim（`m6a_harness.py` 范式，可控多轮唤醒，勿拿真 CLI 烧 token 空转——任务书 §7 纪律 8 原话）。
- **win32 注意**：真 claude CLI 需 stream-json `--verbose`、排空 stderr；git stdout 显式 UTF-8；结束杀树 `taskkill /F /T`（GIT-CALIBRATION 全族）。

### 0.2 成员与项目准备（执行前一次建齐）

**并发规模硬性条件（owner 拍板）**：多 Agent/多节点场景下**至少 6 个真 CLI 窗口同时在跑**。系统模型是一 Agent 一长驻进程（契约 E/E2），故成员池按 6 实现者 + 2 Orchestrator = **8 个长驻 CLI 进程**建齐；T-A′ 与 T-F′ 的 DAG 并行宽度按「峰值 ≥6 个 Agent 同时活跃」设计。

| 成员 | runtime | 用途 |
| --- | --- | --- |
| Orch-Main | claude | 频道 X/Z 共享 Orchestrator（测单点排队形态） |
| Orch-Y | claude | 频道 Y/W 共享 Orchestrator（与 Orch-Main 真并行） |
| Dev-Claude-1 / 2 / 3 | claude | 实现者 |
| Dev-Codex-1 / 2 / 3 | codex | 实现者（**真 codex CLI 已安装并登录**，probe_codex 绿） |
| 人类 owner | — | 确认草稿 / 裁决 needs_human / 部署点击 |

8 进程长驻是本机（win32 单机）从未跑过的规模，本身即被测面：daemon 多连接稳定性、内存/CPU 足迹、全员同时唤醒时 hub 读循环与 SQLite 单写者的表现（M8a 地基的规模上限初探）。执行中用任务管理器/`tasklist` 定时截图留证。

**模型档位（成本杠杆）**：创建 Agent 时 `model` 为必填手动字段，原样透传 runtime（claude 侧 `--model`，codex 侧 JSON-RPC `model`）。建议：6 个实现者统一填 **sonnet** 档（claude 侧）/ codex 默认档——控 6 窗并发成本；两个 Orchestrator 填高档（拆解质量优先）。无「自动选模」能力，见方案外登记。

评审者不单设成员：交叉评审直接复用 Dev-*（checker≠doer 由节点 owner 错开保证）。

非 coding 研究任务复用 Dev-Claude-*（真 claude CLI 自带 WebSearch/WebFetch）；若技能白名单（M5a）对搜索类技能有门，执行前在频道设置放行。

| 项目 | 绑定频道 | 用途 |
| --- | --- | --- |
| P1（记账应用，scratch git 主干可部署） | X | T-A′/T-G/T-B/T-C 主战场 |
| P2（中等静态站） | Y | T-F′ 并行 |
| P3（小工具） | Z | T-F′ 并行 + 同 Project 双频道 409 场景另行临时加绑 |
| —（无 Project） | W | T-H/T-F′ 研究频道：**刻意不绑 Project**，验证纯任务流编排（非 writes_code 节点无需 project_id） |

### 0.3 已核实的架构事实（测试设计依据，勿重复求证）

- 画布每频道恰一张（`canvases.channel_id UNIQUE`）；落地抑制频道粒度；提案唯一约束任务粒度 → **多频道并行是设计内能力**。
- 频道↔Project 多对多（`channel_projects` 复合 PK）。
- 部署 409 串行 = **Project 粒度**（单一非终态索引）：跨 Project 并行合法，同 Project 互斥。
- codex Agent 当 Orchestrator/实现者/评审者**全语义同 claude**（M6 核对，BYO O1）；16 工具 runtime 无关。
- 评审语义现成：TaskHandoff `review_verdict` 四值（pass/downgrade/send_back/needs_human），needs_human 自动 @人类。
- 非 coding 交付通道现成：16 工具含 `upload_file`/`get_file`/`send_message`/`search`；非 writes_code 节点不派 worktree，产出走文件附件 + TaskHandoff；web 检索靠 runtime 自带工具（claude CLI WebSearch/WebFetch）。
- **真机空白**（本方案的核心价值）：真 codex 走 writes_code→worktree→merge 全链、O8 全链、四条 verdict 路径、跨频道并发强度——历届 verify 全是 daemon-sim 或单频道。

---

## 1. 任务总览与执行顺序

| 序 | 任务 | 测什么 | 真 token 成本 | 前置 |
| --- | --- | --- | --- | --- |
| 1 | T-D 烂需求 | 校验回路失败姿态 | 低（红例走 sim） | — |
| 2 | T-A′ 混合 runtime DAG | 拆解规模 + codex 全链 + 并行交付 | **高** | T-D 过 |
| 3 | T-G 评审编排 | review_verdict 四值 + 交叉评审 | 中（叠在 T-A′ 上） | T-A′ 进行中即可叠加 |
| 4 | T-B 故意撞车 | 冲突自动派回链 | 中 | T-A′ 的 DAG |
| 5 | T-C 中途变更 | delta 增量链 + 部分接受 | 中 | T-A′ 落地过半 |
| 6 | T-E O8 汇总护栏 | partial/摘要/stall/阻断-恢复 | 低（红例走 sim） | 独立 |
| 7 | T-H 深度研究长报告 | **非 coding 编排全链**：无 Project 频道 + 研究 DAG + 文件交付 + 长报告成稿 | 中–高（真检索烧 token） | 独立 |
| 8 | T-F′ 多频道并行压测 | 隔离性 + SQLite 并发地基 + **coding/非 coding 混跑** | **高** | 前七项全过 |

失败即停原则：T-D/T-A′ 是地基，不过则后续全停，先修再续；T-G 之后各项相互独立，单项失败登记后可继续。

---

## 2. 任务详单

### T-D 烂需求 → 修复循环与失败升级

**需求原文（频道 X 顶级消息，@Orch-Main）**：

> @Orch-Main 帮我拆解：做一个配置同步工具，模块 A 负责把本地配置推给模块 B，模块 B 校验后回写给模块 A 作为模块 A 的启动输入。两个模块必须由不同的人并行开发、互为前置。

**分支 1（真 CLI，测自愈）**：真 Orchestrator 大概率会识别矛盾并产出合法拆解（如拆掉环）或在线程里追问——两者都算过。若产出非法拆解，观察修复循环信封（含 hint）是否让它一轮自愈。

**分支 2（daemon-sim，测确定性红例）**：sim 固定回放成环拆解 → 校验拒绝 → 修复循环每 rev 2 轮 → 第三败提案 failed + @人类。

**验收 checklist**：
- [ ] 非法拆解被 V1–V14 拒绝，错误信封进线程且含 hint
- [ ] 修复循环轮数恰 2/rev，rev 链在提案卡可见
- [ ] 第三败：提案 failed、@人类消息到达、无残留草稿层
- [ ] 全程库中无悬挂非终态提案（终了核查 proposals 表）

---

### T-A′ 混合 runtime 多层 DAG（主战场）

**需求原文（频道 X，@Orch-Main）**：

> @Orch-Main 在项目 P1 里做一个个人记账 Web 应用，请拆解并行开发：
> ① 数据模型与存储层；② 记账 API、统计 API、预算 API 三个后端接口（依赖①，三者可并行）；③ 录入页、账单列表页、图表页三个前端页面（依赖②对应 API，三者可并行）；④ 汇总报表页（依赖统计 API 和图表页）。
> 后端节点（①②）请指派给 Dev-Codex-1 / Dev-Codex-2 / Dev-Codex-3，前端节点（③④）指派给 Dev-Claude-1 / Dev-Claude-2 / Dev-Claude-3。

预期形态：10–14 节点、3–4 层深、含系统 check/merge 节点；**②③ 相邻两层滚动推进时峰值 6 个实现者同时活跃**（≥6 窗口硬性条件在单频道内即达成）。若 Orchestrator 指派不合意，**在草稿确认层用调整 op 改 owner**（这本身是被测面）。

**验收 checklist**：
- [ ] 拆解一次通过或一轮自愈；草稿层调整 owner 后服务端重验通过
- [ ] 确认 202 → 步进落地全绿（账本逐 op，:done 恰一次）
- [ ] ≥2 个 codex 节点真机认领 → worktree 派生 → 交付产物（**codex 全链首次真机实证**）
- [ ] claude/codex 交付在同一 merge 链上按 DAG 序 `--no-ff` 交错合并成功
- [ ] 中文任务描述、中文提交信息经 codex 路径零 mojibake（CR-M8-2 修复在 codex 侧复验）
- [ ] codex 长驻进程跨多轮唤醒不掉线；CODEX_HOME 不串号（两 codex Agent 各自会话簿记独立）
- [ ] 末端：预览 iframe 可达 → 人类点部署 → 结果卡 URL + 新账 token 小结；GET /usage 三层数字对得上
- [ ] 前端全程 console 零错误；画布 blocked 推导与实际认领时序一致
- [ ] **峰值 ≥6 个 CLI 进程同时活跃**（tasklist 截图留证）；期间 daemon 8 连接零掉线、server 无撕连接、消息投递无明显积压

**观察点（不设过/不过线，如实登记）**：codex 与 claude 的单节点耗时/token 差异；worktree 指令后台通道在 codex 侧的表现。

---

### T-G 评审节点编排（叠加在 T-A′ 上）

**做法**：T-A′ 需求原文追加一句：

> 每个实现节点完成后增加一个评审节点，由另一个 runtime 的 Agent 评审（Claude 写的 Codex 审、Codex 写的 Claude 审），评审结论按 review_verdict 结构化填写。

**诱导三条 verdict 路径**：
- `pass`：正常节点自然走通（多数）。
- `send_back`：挑一个节点，需求写明「列表页必须支持按月份筛选」，但在草稿确认时把该节点描述里的筛选要求删掉（调整 op）——实现者不做筛选，评审者对照原始验收标准应退回。
- `needs_human`：挑一个验收标准写成主观模糊（「图表页配色需美观大方」），预期评审者判 needs_human 升级。
- `downgrade` 不强求，触发则登记。

**验收 checklist**：
- [ ] 评审节点作为普通节点（非 writes_code）落画布，deps 指向实现节点，gating 正确（实现未 done 评审不被唤醒）
- [ ] 交叉评审双向各至少 1 例（Claude 审 Codex / Codex 审 Claude）
- [ ] `pass` → merge 链继续，无多余人工介入
- [ ] `send_back` → 退回重做真实发生 → 二次交付 → 二次评审通过
- [ ] `needs_human` → @人类消息到达，人类裁决后链路继续
- [ ] TaskHandoff 中 review_verdict 字段结构化可查（非纯文案）

---

### T-B 故意撞车 → 冲突派回

**做法**：T-A′ DAG 中②的两个 API 节点，需求各自写明「在共享的 `routes/index`（或等价注册点）注册自己的路由」——两个并行 worktree 必然改同一文件相邻位置。

**验收 checklist**：
- [ ] 先合者 merge 成功；后合者冲突被识别
- [ ] 冲突**自动建任务派回原 owner**（不是静默失败、不是 @人类兜底）
- [ ] 派回任务线程携带足够上下文（冲突文件/分支信息），Agent 修复后重新交付
- [ ] 重试 merge 成功，merge_commit 持久；仅 failed 可 retry 语义未被绕过
- [ ] 期间其余并行节点交付不受阻塞（冲突只锁该支线）

---

### T-C 中途需求变更 → delta 增量链

**做法**：T-A′ 落地过半（③前端节点进行中、④未开工）时，在任一相关任务线程发：

> 需求变更：砍掉汇总报表页，新增 CSV 导入、CSV 导出两个功能节点（都依赖记账 API）。

**验收 checklist**：
- [ ] Agent 发 `<control>` decomposition-delta.v1，五步校验通过，delta 面板绿红高亮正确
- [ ] 人类**部分接受**：剔除 1 个 op（如剔除「导出」）→ 服务端重验 → 剔除清单进线程
- [ ] 落地走共享步进 runner，序正确（remove_edge→remove_node→add_node+入边→add_edge）
- [ ] 删除的「报表页」节点若已被认领 → 锁内重验拒删（NODE_ACTIVE），错误可读；未认领 → 干净移除
- [ ] **base 过期场景**：delta 未确认时先落地另一个小 delta → 确认旧 delta 得 409 + 提案 failed + 要求重出，hint 携当前基线
- [ ] 落地期间系统节点认领抑制生效（running 批期间 merge/check 不被认领）

---

### T-E O8 汇总护栏全链（= M8-HANDOFF §9b #11 原文场景）

**做法**：独立频道小拆解（4–6 节点 + 汇总节点）。护栏红例（空转/触顶）用 **daemon-sim 可控多轮唤醒**；partial 放行与总报告用真 CLI。

**验收 checklist**（逐条对 §9b #11）：
- [ ] 多节点拆解落地，汇总节点默认 partial 档、画布 badge 正确
- [ ] 人为 Close 一个上游节点 → partial 放行，汇总照常唤醒
- [ ] 摘要系统消息生成（指纹未变不重发）；SummaryCard 卡片化渲染
- [ ] Orchestrator 总报告**含未覆盖标注**（Close 掉的节点被如实列出）
- [ ] 制造空转 → stall 计数推进，横幅轮数 N/8 实时刷新（wsBridge qk.thread 失效链）
- [ ] 触顶 → blocked_at 阻断 + @人类；横幅显示阻断原因与计数
- [ ] 人类发言 → 恢复，下一轮摘要重亮横幅；force-start 恢复路径同样走通
- [ ] NodeInspector 人类改档 strict↔partial 生效；Agent 尝试改档被 403 rule=O9

---

### T-H 深度研究长报告（非 coding 编排全链）

**需求原文（频道 W——无 Project 绑定，@Orch-Main 或 Orch-Y）**：

> @Orch-Main 请拆解一个调研项目：主题「2026 年主流开源 Agent 编排框架现状」。
> ① 三个子方向并行调研：框架生态与活跃度、编排范式对比（图式/对话式/工作流式）、生产落地案例与踩坑；每个方向产出带来源引用的调研笔记（markdown 文件交付到本频道）；
> ② 汇总节点收拢三份笔记；
> ③ 成稿节点基于汇总产出一份结构完整的长报告（背景/方法/三方向发现/对比结论/建议），文件交付；
> ④ 报告完成后由另一名 Agent 评审：核对报告是否覆盖三份笔记的要点、引用是否可溯源，按 review_verdict 结构化填写。

预期形态：6–8 节点、三层深，**全部非 writes_code**；汇总节点走 O8 语义（partial 档天然适用——某一子方向失败时报告应带未覆盖标注成稿）。

**验收 checklist**：
- [ ] 无 Project 绑定频道上拆解/落地全链走通（非 writes_code 节点不派 worktree、不触发 check/merge 系统节点）
- [ ] 三个调研节点并行认领，各自真实执行 web 检索（消息线程可见检索过程），产出笔记经 `upload_file` 交付、附件卡正常渲染
- [ ] 汇总节点被正确 gating（三笔记未齐不唤醒）；人为 Close 一个子方向 → partial 放行 → 最终报告**含未覆盖标注**（T-E 语义在真实场景复验）
- [ ] 成稿节点产出长报告文件：结构完整、三方向内容真实取材自上游笔记（非凭空生成）、引用可溯源
- [ ] 评审节点按 review_verdict 结构化裁决；send_back 时成稿节点重做可走通（不强求触发，触发则登记）
- [ ] 长文本/中文报告经 MCP `upload_file` 往返零 mojibake、无截断（大载荷是 UTF-8 修复族未测过的尺寸档）
- [ ] GET /usage：本频道 token 归属正确（研究任务通常远高于代码任务，核对新账口径不失真）

**观察点**：真 CLI 检索轮数与 token 消耗（为今后研究类任务的护栏预算提供实测基线）；调研节点长时间运行（>10min）下沉默提醒/循环 Reminder 是否误触发。

---

### T-F′ 多频道多项目并行压测（收官）

**矩阵**：四频道同时开跑（三 coding + 一非 coding 混跑），全程不串行人工干预（各频道自然节奏推进）。

| 频道 | Project | Orchestrator | 实现者 | 附加面 |
| --- | --- | --- | --- | --- |
| X | P1 | Orch-Main（共享） | claude×2 | 复用 T-A′ 规模拆解 |
| Y | P2 | Orch-Y（共享） | codex×3 | 中等拆解（5–8 节点） |
| Z | P3 | Orch-Main（共享） | 混合（claude×1 + codex 复用）+ 交叉评审 | 小拆解 + 评审节点 |
| W | 无 | Orch-Y（共享） | claude×1 | **非 coding 研究拆解**（T-H 缩小版，3–4 节点）——coding/非 coding 混跑同一批 Agent 基建 |

全部 6 实现者 + 2 Orchestrator = **8 进程全员上场**；四频道错峰起跑（间隔 ~2min）保证中段出现全员同时活跃窗口。

**验收 checklist**：
- [ ] 四张画布/各自落地链/各自摘要**零串台**（抽查：X 的 landing 消息不出现在 Y/Z/W；WS 事件频道归属全对）
- [ ] 共享 Orch-Main 对 X/Z、Orch-Y 对 Y/W 的唤醒各自表现为**排队但零错误**（登记排队时延，不设指标线）；两 Orchestrator 之间真并行
- [ ] **coding 与非 coding 混跑互不干扰**：W 频道长时研究节点占用 Agent 期间，不阻塞其他频道任务投递；W 无 Project、零 worktree/零部署面，不出现误派 worktree 或误触发 check/merge
- [ ] 三 Project 各自部署**并行成功**；随后把 P3 加绑到 Y 频道、双频道同时部署 P3 → 后发者 **409** 且文案可读
- [ ] 全程 server 日志无撕连接/无 busy timeout 级联（M8a 读循环地基首次跨频道强度实证）
- [ ] **全员活跃窗口实证**：某时段 ≥6 个 CLI 进程同时执行任务（tasklist + 前端在线态截图对照）；8 长驻进程全程零掉线，机器资源（内存/CPU）登记峰值
- [ ] GET /usage 三层：workspace 总数 = 四频道分项之和；agent 级归属无串台（研究任务大 token 量不挤歪代码频道账目）
- [ ] 结束核查：无孤儿进程（dev server/CLI/预览）、库无悬挂非终态（proposals/summary_runs/deployments/landing_batches）

---

## 3. 证据与归档

- **边测边截图纪律（owner 拍板）**：截图与测试同步进行，checklist 每项勾销时当场截对应画面，**不允许跑完事后补拍**（状态已流转，补拍不可信）。统一存 `docs/verify/m8-realtest/`，命名 `<任务>-<序号>-<要点>.png`（如 `TA-03-codex-worktree-claim.png`）。每任务的必截时点：拆解提案卡 / 草稿层与确认条 / 落地账本推进 / 画布节点状态翻转 / merge 结果与冲突派回消息 / 预览 iframe / 部署结果卡 / O8 横幅三态 / tasklist 进程清单（并发场景）。
- 每任务一节进 [M8-EVIDENCE.md](M8-EVIDENCE.md)：结论行（PASS/FAIL/PARTIAL）+ 关键截图（owner 偏好：结论要截图实证）+ 涉及消息/任务的 ULID 清单。
- 发现缺陷：当场登记「现象/最小复现/初判归属」，**不当场修**（除非阻塞后续任务），收敛后按 CR-M8 体例开修复批。
- 可自动化的部分（红例、409、库核查）沉淀进 `scratchpad/m8_verify.py` 探针，供 L13 收口复跑；纯人工部分（真 CLI 拆解质量、评审判断力）只留证据不强求脚本化。

## 4. 成本控制

- 真 token 集中在 T-A′/T-G/T-H/T-F′（约占全方案 85%）；执行前确认 claude/codex 两侧额度——**6 实现者并发意味着 token 消耗速率约为单窗的 6 倍，额度按此预留**。
- T-D 分支 2、T-E 红例强制 daemon-sim（零 CLI token）。
- T-H 调研节点在需求原文里限定「三个子方向」与产出形态，防真 CLI 发散检索；T-F′ 的 W 频道用 T-H **缩小版**（3–4 节点），完整研究链只在 T-H 跑一次。
- T-F′ 的 Y/Z 频道拆解刻意压小规模——并行性靠频道数不靠单频道节点数。
- 任一任务真 CLI 出现空转迹象（同一线程 3 轮无进展）立即人工介入止损，勿等护栏烧满。
