# M7 预览、部署与打磨 —— 任务书（M7-HANDOFF）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-13，M6 收口（`d517624`→docs `c775398`）后立项；契约修订核对与落笔已随本次立项**先行完成**（§1）；owner 六项拍板已定（§7 #1–#6） |
| 用途 | **M7 里程碑的唯一任务书入口**：把交付闭环的最后一公里装进系统——每任务独立预览（FR-11）、一键部署与成本核算（FR-12）、性能小批与多租户预留位审查。**M7 收口 = PRD 全部规划里程碑（M1–M7）完成**。前置任务书 [M6-HANDOFF.md](archive/M6-HANDOFF.md) 已完成归档 |
| 上游事实源 | [engineering_docs/](../../../engineering_docs/README.md) 六契约（**A v1.0.11 / B v1.5 / C v1.0 连续零修订核对 / D v1.0.4 / E v1.5 / E2 v1.0.1 零修订核对**；本次修订摘要 §1）· [CoAgentia-PRD.md](../../../docx_agenthub/CoAgentia-PRD.md) FR-11/FR-12/W4/W7/R8/§8 M7 行/§9 非目标 · [交互说明.md](../../../docx_agenthub/03-设计文档/交互说明.md) §12 预览与部署 · [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) |
| 执行计划 | **已建**：[M7-DEV-PLAN.md](M7-DEV-PLAN.md)（波次编排 + 进度表 + 防返工锚点；随模块完成更新）；协作模式 = [COLLAB-MODEL.md](COLLAB-MODEL.md) **v2 续用**（owner 拍板 #4：Fable 单窗编排，执行/评审派 Opus 子代理） |
| 出口标准 | **PRD M7 出口**端到端演示：需求消息 → 拆解 → 执行 → **Diff/预览验收** → 合并 → **一键部署得到线上 URL + 成本小结**（§9 逐条清单） |

---

## 0. 一句话目标

M6 让系统长出总控与手；M7 让交付闭环走完**最后一公里**：合并进主干的代码能被**看见**（每任务独立预览，验收不再靠想象）、能被**发布**（一键部署拿到线上 URL，Agent 也能按下按钮）、能被**算账**（这批新功能花了多少 token，诚实标注覆盖率）——"从对话到上线"的产品承诺（附录 A PreviewSession/Deployment 行）就此闭环，MVP 全部规划里程碑收口。

## 1. 契约修订摘要（**已全部落笔**，2026-07-13 随立项完成——纪律 1 的完成态而非待办）

| 契约 | 版本 | 本次修订内容 | 状态 |
| --- | --- | --- | --- |
| A 实体表 | v1.0.10 → **v1.0.11** | ① preview_sessions 增 `fail_log_tail` 列（交互 §12 失败日志尾数据源）+ **活跃唯一不变量**（同 task_id 至多一活跃行，部分唯一索引）；② deployments **「串行排队」措辞对齐**——立项核对发现 A 与 B/D 矛盾（A 说 queued 排队、B/D 说 409 不排队），钉死 queued=已受理待 ack 瞬时态、至多一个非终态、**409 拒绝不排队** + 单一非终态部分唯一索引兜底；③ token_summary 口径钉死「新账」。**迁移两批 = 0010（M7a preview_sessions）/ 0011（M7b deployments）** | ✅ 已落笔并同步 K0 |
| B REST | v1.4.3 → **v1.5** | 新增 **§13 预览与部署规范条文**：预览生命周期（POST=**ensure+touch 幂等**+前端心跳/GET 纯读/回收调度归 server 三触发/failed 重试=再 POST/iframe 端口数据源/预览不设 Agent 工具）、部署触发与执行（R8 无角色校验/请求空体 branch·commit=server 触发时直查主干 HEAD/command 快照留痕/409 不排队+DB 兜底/结果卡 card_kind=deployment）、部署日志（实时 WS 订阅流+历史 server 直读落盘不依赖 daemon）、**§13.4 成本口径**（GET /usage 三层响应形状冻结 + token_summary 新账推导规则 + 终态 rollup=实时推导零物化）；§4.11 M7 三行形状冻结；**错误码零新增**（DEPLOY_IN_PROGRESS/DAEMON_OFFLINE = v1.0 预留兑现，命令未配置复用 422 VALIDATION_FAILED） | ✅ 已落笔并同步 K0 |
| C WS | — | **零修订**（连续核对，M5 起四届）：`preview.updated` / `deployment.created·updated` / `deployment.log` 订阅流（100ms/2KB）/ `token_usage.reported` **C §6/§7/§8 自 v1.0 起已全登记**——M7 零新增事件类型（K0 核对 contracts ws 目录，未登记者补齐属 M1 预留兑现非契约变更） | ✅ 核对完 |
| D daemon | v1.0.3 → **v1.0.4** | 预览/部署帧从"登记目录"转"落地形状"（与 v1.0.3 M6 交付帧同一节奏）：§4.4 对账 **+2 行**（#9 预览纠偏=失进程置 failed 不自动重拉；#10 部署纠偏=**queued 安全重发 / running 失进程不重跑**——部署副作用不可重放，fail-closed @触发者人工核实）；§5.3 `preview.start` 执行约定（**PORT 环境变量注入** dev_command / 端口轮询健康检查超时 120s / win32 taskkill 杀树 / 存活监控归 daemon）+ `deploy.run` 执行细化（主工作区/commit_hash 仅留痕/超时 30min 同 check.run）；§7 `preview.status` 扩 `log_tail?`。**无新增帧类型**（三指令三上报均 v1.0 预留兑现） | ✅ 已落笔 |
| E 适配器 | v1.4 → **v1.5** | **M7 工具组：新增 `trigger_deploy`**（owner 拍板 #5）——R8「部署全员含 Agent」的通道兑现，§3 M3+ 扩展注记的预定路径；**零工具连胜止于 M6 四届**（纪录是荣誉不是规矩）。REST 纯代理同映射纪律、错误结构化透传、结果卡被动触达不设轮询工具；预览与成本读面**不设工具** | ✅ 已落笔 |
| E2 Codex | — | **零修订核对**：工具目录经 `mcp_command()` 原样复用（工具是 REST 纯代理 runtime 无关，E2 §2.3 既定）——codex Agent 同获 trigger_deploy，零专属代码 | ✅ 核对完 |

> **纪律 1 完成态（K0）**：契约文档修订已完成，contracts manifest/mock/conformance 同步随 K0 执行；业务实现从 K1 起消费，不再猜形状。

## 2. 范围与非目标（勿扩界）

**范围**（PRD §8 M7 行 + owner 拍板 #1「PRD M7 原样」+ #3「性能小批+审查」）：

- **预览链**（FR-11.1–11.3，B §13.1 + D §5.3/§4.4 #9）：daemon 在任务 worktree 起 dev server（PORT 注入/端口自动分配/健康检查/存活监控）；POST ensure+touch 幂等 + 前端心跳；回收三触发（idle 超时/任务终态/cleanup 前置）归 server 调度；失败日志尾 + 重试；iframe 面板可并排多任务对比；FR-11.4 跨机端口代理**仅预留**（D §12 #3，不实施）
- **部署链**（FR-12.1–12.2，B §13.2/§13.3 + D §5.3/§4.4 #10）：POST 触发（R8 全员含 Agent 无角色校验/Idempotency-Key/409 不排队+DB 兜底）；deploy.run 执行 + 日志流式上报落盘 + WS 订阅流 + REST 历史翻页；deploy.finished 结果卡（card_kind=deployment，URL/耗时/退出码）发绑定频道；**Agent 触发通道 = `trigger_deploy` MCP 工具**（E v1.5）
- **成本核算**（FR-12.3，B §13.4）：`GET /usage` 三层聚合（task/agent/canvas + rollup 明细 + tasksReporting 覆盖率，永不折算货币）；**deployments.token_summary 新账口径**（上次 success 部署以来 merged 任务集，纯 SQL 推导触发时落列）；部署卡附小结
- **性能小批**（owner 拍板 #3；挂账收编四件）：CR-9 `_post_landed_message` N+1 / hub usage.batch N+1 / serialize `_plan_skeleton` N+1 / search 双扫
- **预留位审查**（PRD M7 行「多租户/Joint Channel 预留位审查」）：workspace_id 全实体面/频道跨工作区引用位/多机预留（computer_id 路由/端口代理/repo_path 校验路径）盘点成文，**审查不实施**
- **0010/0011 建表批次**（A §5）：M7a preview_sessions / M7b deployments

**非目标**：部署托管与 Vercel/Netlify 专用适配器（FR-12.4 P2；BYO 部署=只执行用户命令）· 多租户/Joint Channel/多人类**实施**（非目标 #2/#11——本里程碑只审查预留位）· 跨机端口代理实施（FR-11.4 预留）· O8 汇总执行期护栏/递归拆解/拆解质量回路（**汇总设计另文**，M8 立项动作——拆解设计 §10/§17 既定）· worktree 定向 check/行级 Diff 评论（非目标 #10）· usage 独立页面（裁决 #15：三层读面落点=端点+部署卡+画布汇总条，全页搜索式独立视图不做）· preview 对 dev server 的日志流（MVP 只留失败日志尾；运行期日志看 daemon 诊断）。

### 2.1 执行切分（两块竖切，体例同 M2–M6；owner 拍板 #2：预览先行）

| 块 | 定位 | 模块 | 收口意义 |
| --- | --- | --- | --- |
| **M7a 预览链** | 先收 | K0 · K1 · K2 · K3 ＋ B-M7-1 | **不靠部署即可完整演示「Diff/预览验收」**：任务交付 → Diff 卡评审 → 预览面板真机打开 dev server → 并排对比 → 合并。daemon 长驻 dev server 子进程管理（全里程碑最大新风险面：端口/健康检查/孤儿进程）最早暴露 |
| **M7b 部署、成本与收尾** | 块 a 收口后开工 | K4 · K5 · K6 · K7 · K8 · K9 ＋ B-M7-2 | **收口即 PRD M7 出口 = MVP 全里程碑完成**：合并后一键部署（人类点击 + Agent 工具双通道）→ 日志实时滚动 → 结果卡 URL + 新账 token 小结；性能小批与预留位审查随块收尾 |

- 切分依据：块 a 全是**长驻子进程与生命周期**心智（端口/健康检查/回收调度/心跳），块 b 全是**一次性执行与账务**心智（命令跑完落账/日志落盘/聚合口径）——两类心智不混批；且部署演示天然发生在预览验收之后（出口场景顺序）。
- 迁移两批：0010 随 K1（块 a）、0011 随 K4（块 b）——A v1.0.11 已裁决，沿 M6 0008/0009 先例。
- 模块编号 **K 系列**（J 已被 M6 占用）。

## 3. 现有资产盘点（拿来即用，勿重复建设；2026-07-13 立项实核）

| 资产 | 位置 | 状态与用途 |
| --- | --- | --- |
| daemon 子进程管理先例 | daemon adapters（agent 进程生命周期/崩溃拉起）+ `check.run` 处理器（30min 超时/run_id 幂等）+ git.py（子进程封装） | K2 dev server 管理照 check.run 体例扩**长驻**变体；win32 杀树/编码坑 = `scratchpad/GIT-CALIBRATION.md` 已校准结论 |
| worktree 域全套 | worktrees 表 + ensure/cleanup 处理器 + 激活联动 + keep_days 清理调度（M6a） | 预览的宿主：worktree_path 取自 worktrees 行；**cleanup 调度是回收三触发之一的挂接点**（K3 在 cleanup 前置回收预览） |
| projects 配置字段 | models.py projects 行：`dev_command` / `deploy_command` / `preview_idle_min` / `worktree_keep_days`（**M6a 0008 已建列**）+ P12 设置 UI（B-M6-1 已交付） | K2/K3/K4 直接读列零迁移；前端设置面**零新增**（M6a Project 弹窗已含命令输入） |
| hub 周期调度先例 | hub 后台扫描（沉默提醒/keep_days 清理/landing loop 15s/对账 60s） | K3 预览 idle 回收 = 同一调度器心智再挂一项；对账 #9/#10 = reconcile 既有挂接点扩两条 |
| daemon 缓冲重传骨架 | daemon buffer（diagnostics/usage JSONL 落盘 + ack 重传）；**deploy-log.jsonl 缓冲位 D §9.1 自 v1.0 已预留** | K4 deploy.log 批照 usage.batch 体例挂缓冲类；chunk_seq 去重语义 D §7 已写死 |
| card_kind 目录 | contracts `card_kind` 含 **deployment**（A §4.2 自 v1.0 登记）+ persist_message 两相挂接（M6b） | K4 结果卡零枚举变更；前端卡片判定接线照 MERGE_CONFLICT 先例 |
| usage 聚合先例 | `token_usage_events` 表（禁 UPDATE/DELETE 触发器）+ tasks.py:414 TaskDetail.usage 按 task 聚合 + `token_usage.reported` 广播 | K6 GET /usage 三层 = 同一聚合 SQL 三个 GROUP BY 维度；token_summary 新账 = worktrees.merged_at 区间联查 |
| MCP 工具登记先例 | daemon mcp.py + contracts `COAGENTIA_MCP_TOOLS` + catalog 测试（M2 六工具批） | K5 trigger_deploy = 照 claim_task 体例 +1；错误结构化透传（CLAIM_RACE 先例同构） |
| Idempotency-Key 账本 | B §1 幂等重试复用账本（M1 起全写端点先例） | K4 POST deployments 直接复用 |
| WS 订阅流骨架 | ws/hub.py 订阅制（diagnostic 流 sub/unsub；C §8） | K4 deploy_log 流照 diagnostic 体例挂第二个订阅流 |
| wsBridge/卡片前端范式 | wsBridge 事件族接线（M6b draft.*/landing.* 先例）+ Diff 卡/HeldDraftCard/LandingToaster 组件体例 | B-M7-1 预览面板、B-M7-2 部署卡照既有体例；`preview.updated`/`deployment.*` 接线同构 |
| 实机 verify 基建 | `scratchpad/m6a_harness.py` + `m6a_appfactory.py` + `m6_verify.py`（隔离库/真 daemon-sim/scratch git 仓库/taskkill 杀树） | K9 照此扩 M7 探针；dev_command 用 `python -m http.server %PORT%` 类零依赖命令 |

**确认缺口**（施工面）：preview_sessions/deployments 未迁移 · **daemon 长驻 dev server 管理零先例**（端口自动分配/健康检查轮询/存活监控/孤儿进程回收全新——win32 端口占用与子进程树行为未知，**K2-cal 实测校准先行**）· `GET /usage` 端点不存在（routes 无 usage.py）· deploy-logs 落盘与 REST 翻页读不存在 · mcp.py 自 M2 后首次加工具 · web：预览 iframe 面板/部署确认弹窗/部署卡日志滚动/token 小结全新。

## 4. 线 A：后端任务分解（模块 → DoD）

| # | 块 | 模块 | 内容（契约出处） | 完成判据（DoD） |
| --- | --- | --- | --- | --- |
| K0 | **M7a** | 契约登记同步（**开工第一步之一**） | contracts 包：`ENDPOINTS_M7`（preview 3 + deployments 3 + usage 1 = 7 端点）、`PreviewSessionPublic`（含 fail_log_tail）、`DeploymentPublic`、`TokenSummary`（新账嵌套模型，A §8.3 惯例）、`UsageReport`（B §13.4 形状）、D 帧模型 preview.start/stop + preview.status（扩 log_tail）+ deploy.run/log/finished、**`COAGENTIA_MCP_TOOLS` + trigger_deploy**（E v1.5）、**ws 事件目录核对补齐 M7 预留族**（preview.updated/deployment.*/deploy_log 订阅流——M1 预留兑现非契约变更）、mock 补 preview/deployments/usage 形状（纪律 4） | manifest/catalog 测试红转绿；`pnpm gen` diff 为空；mock 一致性扩展全绿 |
| K1 | **M7a** | 0010 迁移 | Alembic `0010_m7a`：preview_sessions 一张（A §4.9 v1.0.11：含 fail_log_tail + **同 task_id 单活跃部分唯一索引**）；models ORM + M7A_TABLES | 从零 `upgrade head` 与 M6 库增量升级双路绿；表结构对照测试扩展；部分唯一索引红例（双活跃行被拒） |
| K2 | **M7a** | daemon 预览进程域（**K2-cal 实测校准最先做**） | D §5.3 v1.0.4：dev server 子进程管理——空闲端口获取 + **PORT 环境变量注入** dev_command + 健康检查轮询（TCP 可达→running 携 port；超时 120s→杀树+failed 携 log_tail ≤2KB）+ 存活监控（进程退出→failed 携 log_tail）+ win32 taskkill /F /T 杀树；preview.start/stop 处理器（自然键幂等：已在跑 noop+上报端口/已停 noop）+ preview.status 上报（载状态） | **K2-cal 结论落 DEV-PLAN**（win32 端口分配竞态/子进程树/孤儿回收坑清单）；start 幂等重发 noop+现状；健康检查成/超时/进程夭折三路径；stop 幂等；daemon 重启后孤儿 dev server 不残留（cal 验证） |
| K3 | **M7a** | server 预览域 | B §13.1：`POST /tasks/{id}/preview`（**ensure+touch**：无活跃建行+下发 start/已活跃仅推进 last_active_at；无 worktree 404/离线 503/无 dev_command 422 details+hint）+ GET（纯读）+ DELETE（下发 stop）；preview.status 处理（行推进/failed 落 fail_log_tail/广播 preview.updated + diagnostic）；**回收调度**（hub 周期扫描 idle 超 preview_idle_min → stop；任务终态即回收；**worktree cleanup 前置回收**）；对账 #9（活跃行失进程→failed 不重拉/starting 超时同口径） | ensure/touch 两语义逐例；三回收触发各一例；404/503/422 拒绝路径；对账 #9 断连恢复例；preview.updated 广播断言；单活跃索引竞态（并发双 POST 恰一行） |
| K4 | **M7b** | 0011 迁移 + 部署域 | Alembic `0011_m7b`：deployments 一张（A §4.9 v1.0.11：**同 project_id 单一非终态部分唯一索引**）；B §13.2/§13.3：`POST /projects/{id}/deployments`（R8 无角色校验/**409 DEPLOY_IN_PROGRESS 竞败=索引兜底**/Idempotency-Key 账本复用/branch·commit_hash server 直查主干 HEAD/command 快照）→ 下发 deploy.run；deploy.log 处理（追加 `deploy-logs/<id>.log` + chunk_seq 去重 + 转发订阅流）+ `GET /deployments/{id}/log?after=`（server 直读落盘，`{lines, next_after, truncated}`）；deploy.finished 处理（行终态+deployment.updated+**结果卡 card_kind=deployment 发绑定频道**各一条+diagnostic）；对账 #10（queued 重发/running 失进程 fail-closed @触发者）；ws deploy_log 订阅流（照 diagnostic 体例） | 端点逐路径（R8 Agent 主体放行/409/幂等键/422 无命令）；并发双触发恰一行（索引竞态）；日志链路（缓冲重传/去重/翻页/truncated）；结果卡多频道断言；对账 #10 两分支（queued 安全重发 noop 幂等/running 置 failed+系统消息）；conformance 双跑扩展 |
| K5 | **M7b** | trigger_deploy 工具 | E v1.5：daemon mcp.py **+1 工具**（M2 后首次）——`trigger_deploy {project_id}` → POST /projects/{id}/deployments 纯代理；DEPLOY_IN_PROGRESS/VALIDATION_FAILED/DAEMON_OFFLINE 结构化透传（CLAIM_RACE 体例）；工具话术（何时部署/部署即公开动作/结果卡进频道——K 收口定稿）；E2 复用核对（codex 经 mcp_command 同获） | 工具目录 catalog +1 绿；透传三错误逐例；Agent 主体真调用链（X-Acting-Member 留痕断言）；E2 零改动核对 |
| K6 | **M7b** | 成本核算 | B §13.4：`GET /usage?level=task\|agent\|canvas&ref=&rollup=` 新 routes/usage.py（三 GROUP BY 维度聚合/tasks_reporting 覆盖率/rollup=true 附 breakdown/永不折算货币）；**token_summary 新账快照**——POST deployments 时纯 SQL 推导（该 Project worktrees.merged_at ∈ (上次 success finished_at, 本次 created_at] 的任务集聚合 + task_ids 有界 50）落列 | 三层各逐例（含空集/未归属计入分母）；rollup 明细形状；新账区间断言（首次部署无下界/二次部署只算增量/失败部署不推进区间）；快照落列后配置变更不影响留痕 |
| K7 | **M7b** | 性能小批（挂账收编，owner 拍板 #3） | ① CR-9 `_post_landed_message` 逐节点 fetch_task/_member_name → 批量预取；② hub usage.batch 逐条查归属 N+1 → 批查；③ serialize `_plan_skeleton` N+1 → 联查；④ search 双扫 → 单扫。**语义零变更**（纯性能，输出逐字节等价） | 四件各有前后行为等价回归（既有测试全绿即证）；查询次数断言（SQLAlchemy echo 计数或等价手段）各降为 O(1)/O(批) |
| K8 | **M7b** | 预留位审查（文档件） | PRD M7 行「多租户/Joint Channel 预留位审查」：盘点 workspace_id 全实体覆盖面（A NFR8）、频道跨工作区引用位、多机预留三件（projects.computer_id 路由/D §12 #3 端口代理/B §8 #4 repo_path 校验路径）、单进程假设清单（`_landing_lock` 跨进程双直落批等 M6 审计登记项）→ 成文 `docs/M7-RESERVATION-AUDIT.md`（**审查不实施**；发现的破坏性风险登记挂账，M8+ 立项消费） | 文档成文并交 owner 过目；结论进 §8 挂账登记（有则）；零代码变更 |
| K9 | **M7b** | 端到端实机 verify（**块 b 收口 = PRD M7 出口 = MVP 全里程碑完成**） | 真机（m6a_harness 体例：隔离库+独立端口+scratch git 仓库+真 daemon-sim；dev_command=零依赖 http 服务、deploy_command=脚本产出 URL 行）：需求消息 @Orchestrator → 拆解落地 → writes_code 任务 worktree 交付 → Diff 评审 → **预览面板真机打开（健康检查→running→iframe 可达）** → 合并系统节点成功 → **人类点部署 + Agent trigger_deploy 双通道**（第二次触发 409）→ 日志流滚动 → 结果卡 URL + **新账 token 小结** → idle 超时自动回收；顺带：预览失败日志尾（坏 dev_command）、对账 #9/#10 崩溃探针、部署期 daemon 重启 fail-closed | §9a+§9b 清单收口 + 截图/证据归档 `docs/verify/M7-EVIDENCE.md`（PRD M7 出口句逐环节勾销） |

**推进顺序**：**块 M7a**：K0 ∥ K1 并行（contracts / migrations 文件域不相交）→ **K2-cal 实测校准紧随最优先**（win32 长驻子进程与端口行为是全里程碑最大不确定性——先真机戳行为再写处理器，M6 J3-cal 先例）→ K2 → K3（消费 K2 上报）→ B-M7-1 吃 K0 mock 即可开工；**块 M7b**：K4 → K5 ∥ K6 并行（mcp 工具 / usage 端点文件域不相交）→ K7 ∥ K8 并行（收尾件）→ K9 收口。

## 5. 线 B：前端任务分解（模块 → DoP）

> 沿用设计线 verify SOP：优先消费归档稿 `docx_agenthub/04-设计稿/afterglow-ds/previews/`（预览面板/部署卡有稿则对照，无稿按既有组件体例）→ playwright 1440×900 对照 → 复发点自查。

| # | 块 | 模块 | 内容 | 完成判据 |
| --- | --- | --- | --- | --- |
| B-M7-1 | **M7a** | 预览面板 | ① 交付卡/任务详情 **[预览] 按钮**（有 worktree 且 Project 配 dev_command 才亮；点击 POST）；② **预览面板**（iframe 承载 `http://127.0.0.1:{port}`；顶条状态"启动中…（健康检查）"→就绪加载/失败显 `fail_log_tail` 尾 20 行 mono + [重试]=再 POST——交互 §12）；③ **面板打开期间心跳 POST**（60s，关闭即停）；④ **并排多任务对比**（多面板布局，FR-11.2）；⑤ 回收倒计时（`last_active_at`+preview_idle_min 客户端推导，C §6 既定）；wsBridge 接 `preview.updated` | 行为测试（按钮亮灭条件/状态顶条三态/心跳定时器/重试链路/倒计时推导）+ 屏对照截图 |
| B-M7-2 | **M7b** | 部署卡 + 成本面 | ① **[部署] 按钮 + 确认弹窗**（branch@hash/deploy command/触发者——确认制归 UI，交互 §12；进行中 409 → toast「上一次部署进行中」）；② **部署卡**（card_kind=deployment：日志实时滚动**自动跟随+向上滚暂停+「↓ 跟随」胶囊**/结果行 URL·耗时·退出码/失败态色）——`deployment.log` **订阅制**（打开日志视图 sub、关闭 unsub）+ 历史翻页接 `GET …/log?after=`；③ **token 小结行**（新账 Σ 四字段 + tasksReporting=N/M 覆盖率诚实标注，永不显示货币）；④ **画布页签 usage 汇总条**（轻量：GET /usage?level=canvas 数字 chip，裁决 #15 的三层读面落点之一）；wsBridge 接 `deployment.created/updated` + deploy_log 流 | 行为测试（确认弹窗内容/409 toast/日志跟随与暂停/sub·unsub 时机/翻页/token 行/汇总条）+ 屏对照截图 |

## 6. 纪律（沿用 M2–M6 八条，本里程碑强调三点）

1. 契约 ↔ manifest 双向同步（K0 收口中间态；D 帧形状随 K2-cal 实测若需再修，升版回改文档——M5 H2/M6 J3 先例）。
2. **判定归 server、执行归 daemon**（D 铁律 1 在 M7 的形态）：回收三触发/排队 409/健康检查超时阈值的**判定**全在 server 或契约默认，daemon 收指令即执行不复判；daemon 只上报事实（port/log_tail/exit_code）。
3. **副作用可重放性分野**（对账 #10 的心智，本里程碑最重要新纪律）：落地重放（#4）可重放因为幂等键护住；**部署命令不可重放**（外部副作用）——凡"命令跑了一半 daemon 死了"一律 fail-closed 置 failed @人工核实，不自动重跑。预览进程可重建（无副作用）但**不自动重拉**（人开的面板人再点，回收成本低）。
4. 生成物只经脚本重生成，diff 为空守门；一致性测试双跑复用（M7 端点扩 `test_conformance_dual.py`）；mock 是形状源不是逻辑源（健康检查/回收调度/聚合 SQL 只活在真 server/daemon）。
5. 每完成一个模块：更新 M7-DEV-PLAN §进度表 + [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)；阶段结论沉淀 [PROJECT-RECORD.md](PROJECT-RECORD.md)；结论截图实证。
6. Owner 偏好：中文；微瑕直接修、大事选项问；已拍板勿再问（§7 #1–#6 即已拍板项）。
7. 值域/判定语义只写一处：新账区间推导/usage 聚合 SQL 活在 server 单点；前端只消费 UsageReport/TokenSummary 形状，不复算。
8. 实机验证起的 server/浏览器/daemon-sim/dev server 进程结束前必杀（taskkill /F /T）——**本里程碑新增子进程种类（dev server），杀树纪律覆盖到孙进程**。

## 7. 本任务书裁决（实现按此执行；#1–#6 = owner 拍板 2026-07-13，其余 owner 可否决，否决处升契约版本回改）

| # | 裁决 | 依据 |
| --- | --- | --- |
| 1 | **范围 = PRD M7 原样**（FR-11+FR-12+打磨+预留位审查）；O8/递归拆解/质量回路留 M8（先补汇总设计另文） | owner 2026-07-13；PRD §8 M7 行；拆解设计 §10「另文」 |
| 2 | **预览先行**：M7a=FR-11、M7b=FR-12+收尾；出口整合场景落 M7b 收口 | owner 2026-07-13；dev server 进程管理最大风险面早暴露 + 出口场景顺序（预览验收在合并前） |
| 3 | **打磨 = 性能小批四件 + 预留位审查**（K7/K8）；UI 低危观察项不动 | owner 2026-07-13 |
| 4 | **协作模式 = COLLAB-MODEL v2 续用**（Fable 单窗编排；K2/K9 与幂等/事务正确性修复 Fable 亲做，执行/评审 Opus 子代理） | owner 2026-07-13；M6b 全程实盘有效 |
| 5 | **Agent 触发部署 = `trigger_deploy` MCP 工具**（E v1.5；零工具连胜止于 M6）；预览/成本读面不设工具 | owner 2026-07-13；E §3 M3+ 扩展注记预定路径；R8 无审批门，即时动作走工具不硬套 `<control>` 提案流 |
| 6 | **token_summary 口径 = 新账**：上次 success 部署以来 merged 任务集（B §13.4 推导规则；失败部署不推进区间） | owner 2026-07-13；「特性总」语义诚实——回答"这批新功能花了多少" |
| 7 | **deployments「串行排队」= 409 不排队**：queued 仅为已受理待 ack 瞬时态；A 原文与 B/D 矛盾处以 B/D 为准修 A（v1.0.11）——立项核对纠偏，非行为设计变更 | A v1.0.11 ②；D §5.3 deploy.run 行自 v1.0 即写"串行由 server 端点保证"；交互 §12「排队提示」实为 409 文案 |
| 8 | **预览 POST = ensure+touch 幂等 + 前端心跳推进 last_active_at**（60s，面板关即停）；GET 纯读无写副作用 | B §13.1 #1；读写分离；心跳是"面板还开着"的唯一诚实信号（iframe 直连 dev server 端口，server 不在数据路径上） |
| 9 | **dev_command 端口约定 = PORT 环境变量注入**（daemon 分配；命令可引用 `$PORT`/`%PORT%`） | D §5.3 v1.0.4；约定优于配置，业界通行（Heroku/Vercel 同款） |
| 10 | **部署纠偏 fail-closed**：running 失进程不重跑（副作用不可重放），置 failed @触发者人工核实；queued 未 ack 可安全重发 | D §4.4 #10；纪律 3 |
| 11 | **预览纠偏不自动重拉**：daemon 重启后活跃预览置 failed，等人再点（POST ensure 重建）；starting 超时同口径 | D §4.4 #9；人开的面板人再点，自动重拉引入无人观察的常驻进程 |
| 12 | 迁移两批 0010（M7a preview_sessions）/ 0011（M7b deployments）；两处部分唯一索引随各自建表落地 | A v1.0.11；M6 0008/0009 先例 |
| 13 | 结果卡发 Project **全部绑定频道**各一条（briefing 多频道同语义）；日志与详情深链同一 deployment | B §13.2 #5 |
| 14 | 实机 verify = scratch 仓库 + 零依赖 dev/deploy 命令（M6 裁决 #15 沿用）；**不拿 coagentia 仓库当靶子、不真部署外网** | K9；deploy_command 用本地脚本输出伪 URL 即满足"命令执行+URL 提取"全链 |
| 15 | **usage 三层读面落点 = GET /usage 端点 + 部署卡小结 + 画布页签汇总条**；不做独立 usage 页面 | FR-12.3 只名部署卡；TaskDetail token 徽章 M2 已有；最小可见面原则 |

## 8. 挂账（承接 CURRENT-HANDOFF §5；勿当漏项重新发明）

| 出处 | 问题 | 归属 |
| --- | --- | --- |
| ~~M6b CR-9~~ | ~~`_post_landed_message` N+1~~ | **K7 收**（拍板 #3） |
| ~~M2 挂账~~ | ~~hub usage.batch N+1 / search 双扫~~ | **K7 收** |
| ~~M5b 性能~~ | ~~serialize `_plan_skeleton` N+1~~ | **K7 收** |
| M6b CR-10 | proposals 部分唯一索引谓词与终态集双源 | 观察项（M7 新增两索引**同型**——K1/K4 落地时顺路加断言测试钉三处，与 CR-10 一并收） |
| M6b CR-11 | 0009 downgrade agents batch recreate FK 未测 | 择机（downgrade 罕跑） |
| M6b 审计登记 | 跨进程双直落批（单进程 `_landing_lock` 假设） | **K8 审查文档盘点**，实施归 M7 后多机批 |
| M6b 审计登记 | 注入面 prompt 中和（单工作区信任模型内） | 观察项（多用户化前收；K8 盘点提及） |
| M6b 前端观察 | revalidateDelta 子集/TS 潜伏守卫/UTF-16 码元序/终态悬挂/LandingToaster 不分频道/P12 越界 | 观察项不动（拍板 #3） |
| M5b/M4–M2 承接 | briefing @全部 by-design / `_layout_positions` 双实现 / held 倒计时边角 / `task #n` refs / `<TaskBoard>` 抽取 / messages_fts rowid / OAuth 冷启动 | 原归属不变（观察/择机；K9 真机可顺路复验 OAuth——M6 同款顺路项未做则延续挂账） |
| 模板域 | 模板不携 system 节点 / 不提供 Project 重映射 | MVP 观察项 |
| **本任务书新增** | O8 汇总执行期护栏 / 递归拆解 / 拆解质量回路 → **汇总设计另文** | M8 立项动作（拍板 #1） |
| **本任务书新增** | 跨机预览端口代理实施（D §12 #3 预留已带 port） | 多机批 |
| **本任务书新增** | 部署适配器（Vercel/Netlify 专用集成，FR-12.4） | P2 后置 |
| **本任务书新增** | preview 运行期日志流（MVP 仅失败日志尾） | 观察项（需求出现再议） |

## 9. M7 出口验收清单（按块分组；两块全绿即里程碑收口 = **MVP 全里程碑完成**）

### 9a. 块 M7a「预览链」清单

- [ ] 1. K0 契约登记：ENDPOINTS_M7 / PreviewSession·Deployment·TokenSummary·UsageReport 模型 / D 三指令三上报帧（preview.status 含 log_tail）/ trigger_deploy 工具目录 / ws M7 预留族核对 / mock 形状，catalog 与 `pnpm gen` 两跑一致
- [ ] 2. Alembic `0010_m7a`：preview_sessions（含 fail_log_tail + 单活跃部分唯一索引），从零与增量双路绿；对照测试；索引红例
- [ ] 3. K2-cal 实测校准结论落 DEV-PLAN（win32 端口分配/子进程树/孤儿回收坑清单）
- [ ] 4. K2 daemon：PORT 注入 + 健康检查三路径（可达/超时/夭折）+ 存活监控 + start/stop 幂等逐例 + 杀树无孤儿
- [ ] 5. K3 server：ensure/touch 两语义 + 回收三触发（idle/任务终态/cleanup 前置）+ 404/503/422 拒绝路径 + 对账 #9 + 并发双 POST 恰一行
- [ ] 6. B-M7-1：预览按钮亮灭/面板三态顶条/失败日志尾+重试/心跳/并排对比/倒计时 + wsBridge preview.updated，行为测试 + 截图
- [ ] 7. **M7a 真机场景**：scratch repo + writes_code 任务交付 → 预览面板打开 → 健康检查 → iframe 真实可达（HTTP 200）→ 并排第二任务预览 → idle 超时自动回收 / 坏命令失败态显日志尾；截图归档
- [ ] 8. 块 a 守门：后端/前端全量测试、typecheck（pyright 0）、ruff、gen 确定、双侧 build 全绿；交接文档同步（纪律 5）

### 9b. 块 M7b「部署、成本与收尾」清单（全绿 = **PRD M7 出口达成**）

- [ ] 9. Alembic `0011_m7b`：deployments（单一非终态部分唯一索引），双路绿；索引红例；CR-10 同型断言测试三处钉住
- [ ] 10. K4 部署域逐路径：R8 Agent 放行/409 不排队+索引兜底并发恰一行/Idempotency-Key/422 无命令/branch·commit 触发时快照/日志链路（缓冲重传·chunk_seq 去重·翻页·truncated）/结果卡多频道/对账 #10 两分支
- [ ] 11. K5 trigger_deploy：catalog +1 / 三错误结构化透传 / Agent 真调用留痕 / E2 零改动核对
- [ ] 12. K6 成本：GET /usage 三层逐例（空集/未归属分母/rollup 明细）/ token_summary 新账区间断言（首次无下界/增量/失败不推进）/ 永无货币字段
- [ ] 13. K7 性能四件：行为等价回归全绿 + 查询次数断言各达标
- [ ] 14. K8 预留位审查文档成文，owner 过目，发现项登记挂账
- [ ] 15. B-M7-2：确认弹窗/409 toast/日志跟随与胶囊/sub·unsub/历史翻页/token 小结行/画布汇总条 + wsBridge deployment.*，行为测试 + 截图
- [ ] 16. **PRD M7 出口真机实证**（K9 全链）：需求消息 → 拆解 → 执行 → Diff/**预览验收** → 合并 → **一键部署（人类+Agent 双通道，二次触发 409）→ 日志实时滚动 → 结果卡 URL + 新账 token 小结**；对账 #9/#10 崩溃探针；全程 WS 无刷新；截图归档 `docs/verify/M7-EVIDENCE.md`
- [ ] 17. 终收口守门：全量测试绿（基线 955+359 只增不减）；M7 阶段结论写入 [PROJECT-RECORD.md](PROJECT-RECORD.md)，本任务书移入 archive/（README 维护约定 3）；**MVP 全里程碑（M1–M7）完成声明**

## 10. 第一步建议

块 M7a 从 **K0 ∥ K1 并行**开工（contracts 包 / migrations 文件域不相交），**K2-cal 实测校准紧随最优先**——win32 上长驻 dev server 子进程的端口分配、健康检查与孤儿进程回收是全里程碑最大的外部不确定性（M6 J3-cal「先实测后写处理器」先例）：在 scratch 目录用零依赖命令（`python -m http.server`）真机戳 PORT 注入/端口占用竞态/taskkill 树杀/daemon 重启孤儿五组行为并记录坑清单，再写 K2 处理器就是照契约填空；K3/B-M7-1 依 §4 推进顺序跟进。**块 M7b 不与块 a 交错**——等 §9a 全绿再动（部署演示天然发生在预览验收之后），保持"一块 = 一个可交接的收口"。
