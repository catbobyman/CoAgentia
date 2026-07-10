# M1 Review 修复交接

> **2026-07-10 第二批增补：M2 二轮实机 verify + `/code-review high` 修复批收口，M3 已立项。**
> ① **二轮实机 verify**（收口态 `6c12b90` 复验）：番茄钟真 HTTP + 对抗 probe + 聚合面 + 浏览器全流程 **35/35 PASS**，证据 [M2-REVIEW2-EVIDENCE.md](../verify/M2-REVIEW2-EVIDENCE.md)。
> ② **`/code-review high`**（7 finder × 34 去重候选 × 逐项验证）：15 CONFIRMED / 1 PLAUSIBLE / 1 REFUTED，前 10 上报并**全部修复**：activity done 无归属校验（走 acting_member，Agent Bearer 404）· Composer IME 组合态误发 + Ctrl/Cmd+Enter 吞键回归 · api.tasks 首页 50 截断（升序致新任务消失，改游标全量聚合）· 消息流时间 UTC 硬切（新 `lib/time.ts` 统一本地化，收拢 5 处 slice + 2 份 relTime 复制）· ActivityScreen 行为人错位（契约 A **v1.0.3**：ActivityItemPublic 增派生字段 `actor_member_id`）· wsBridge 缺 channelFiles 失效（文件交付实时化）· BoardTab Firefox 拖拽失效（setData）· FilesTab「定位到消息」丢参（补滚动+闪烁高亮）· ThreadPanel 结构性无附件卡（filesByMessage 贯通）· ThreadPanel usage 冻结（token_usage.reported 失效 taskDetail）。**尾部 3 项一并修**：认领钮占用置灰、wsBridge activity 按 owner 过滤、`_generate_activity` 免回读 JOIN；杂项 2：daemon mcp.py 状态枚举改契约派生、冒烟桩形状对齐裸 TaskPublic；api.sendMessage 并入 writeJson 统一错误路径。
> ③ **收口态**：后端 **387 passed, 3 skipped**（+3 回归测试）、ruff 干净、`pnpm gen` 确定性；前端 typecheck/build 绿、vitest 18；修复实机复核（Agent 清他人未读 404 / actor 显示 Pat / 时间本地化 / 线程附件卡 / 认领钮置灰）全过。
> ④ **M3 已立项**：任务书 [M3-HANDOFF.md](M3-HANDOFF.md)（两块竖切：**M3a 契约与校验** → **M3b 画布与 gating**，FTS trigram 浮动件）；M2-HANDOFF 移入 archive/。二轮 review 未修挂账（keyset 分页/activity 全表扫/三档缓存簡化/_emit_activity 层级等）已收纳 M3-HANDOFF §8。

> **2026-07-10 增补：M2 全部完成（后半 C3–C7 + 前端 B 线收口）。** 多 agent 工作流编排：后端工作流（C3a task#n 解析 + C3b Activity 生成 mention/dm、C4 files/search/activity 端点、C5 daemon MCP 六工具、C6 usage 归属富化，4 agent 互不重叠文件并行 + 整合）→ 前端工作流（数据层接真 + Toast + P10 搜索 → fe-channel P1/P5/P3/P4 + fe-workspace P11/P8/P9 并行 → verify）。**纪律 7 补齐**：`TASK_TRANSITIONS` + `UNCLAIMABLE_STATUSES` 由 gen 管线生成到 contracts-ts（`constants.ts`），前端拖列/流转/认领防呆消费同一事实源。**收口态**：后端 `pytest -q` = **384 passed, 3 skipped**（较 M2 前半 340 净增 44）、ruff 干净、`pnpm gen` 确定性；前端 typecheck/build 绿、vitest **18 passed**。**实机 verify**：C7 番茄钟真 HTTP **17/17**（人 As Task→Agent claim 联动→线程汇报→文件交付→in_review→人 done→终态守卫→WS 广播）+ 浏览器 UI 全流程（As Task 无刷新出牌、claim 无刷新移列、P3/P11/P9/P10/P8/P5 全屏），12 张截图 + [M2-C7-EVIDENCE.md](../verify/M2-C7-EVIDENCE.md) 归档。中文 FTS 结论回写契约 A §10.4（unicode61 对 CJK 子串不命中，trigram 归 M3）。**`/code-review high`（8 角度）已跑：10 CONFIRMED → 修 6 + 挂账 4**，明细见 [M2-DEV-PLAN.md](M2-DEV-PLAN.md)。执行计划与逐模块状态 = [M2-DEV-PLAN.md](M2-DEV-PLAN.md)；出口清单 [M2-HANDOFF.md](M2-HANDOFF.md) §9a/§9b 两块全绿。**未提交前请注意**：本批工作树含 M2 后半全量改动（65+ 文件）。
>
> **2026-07-09 增补：M2 前半（C0+C1+C2）已实现并验证。** 工作流编排多 agent 完成：C0 契约登记（enums/entities/rest/constants + mock 形状 + pnpm gen）、C1 建表迁移（Alembic `0002_m2`：tasks/task_events/message_task_refs/activity_items + messages_fts FTS5 + task_events 不可变触发器；0001 拆表清单解 create_all 泄漏坑）、C2 任务域 8 端点（convert/claim/unclaim/assign/status/list/detail/patch + as_task 原子建任务）。全量 **340 passed, 2 skipped**（较 M1 的 238 净增 102 测试含 40 例对抗性硬化 + 2 例 hardening 回归），ruff 干净，pnpm gen 确定性入库，**实机 verify 真 HTTP 24/24 + hardening 复核 6/6**（as_task/convert 幂等/claim 联动/状态机合法·非法·同态·终态/并发 claim 恰一无 5xx/assign/列表/详情）。code-review high 已跑，`default_title` 小数误剥微瑕已直接修复；待决项 1-2（convert TOCTOU / claim 终态门）owner 拍板后已硬化，其余见下方"M2 前半 review 待决项"。**已提交：`42f20f0`（C0-C2 收口）+ hardening 提交。** 第二半 = C3 消息联动 + C4 文件/搜索/Activity 端点 + C5 MCP 工具 + C6 usage 富化 + C7 番茄钟端到端 + 前端 B 线。

## M2 前半 review 待决项

1. ~~**convert 并发 TOCTOU → 500**~~ **已修**（owner 拍板 2026-07-09）：`convert_message_to_task` 建任务段套 SAVEPOINT（范式同 `ledger.record`），`UNIQUE(root_message_id)` 冲突回退本段（含编号自增不漏号）后重查既有任务幂等 200。并发 convert 回归测试 + 实机复核（恰一 201 / 7×200 同任务 / 无 5xx）。
2. ~~**claim 未挡终态**~~ **已修**（owner 拍板 2026-07-09）：`tasks/service.py` 增 `UNCLAIMABLE_STATUSES = {done, closed}`（claim 语义门，非第二份边表），claim 前置校验 → 422 TASK_TRANSITION_INVALID `details{status}`；closed reopen 回 todo 后可正常认领。回归测试 + 实机复核通过。
3. **list_tasks 游标失效重排**（低中，未修）：`after` id 因 status/owner 过滤在翻页间离开结果集时静默从头翻 → 跨页重复项（沿袭 M1 messages 分页模式，此处因可变过滤更易触发）。
4. **patch_task 无法清空 `silence_override_h`**（低，M4 才消费）：`if v is not None` 丢弃 null，无法把任务级覆盖重置回 NULL。
5. 挂账（低）：messages_fts 键于 messages.rowid（ULID PK 的隐式 rowid），未来 VACUUM 会失同步；allocate_number 依赖 SQLite≥3.35 的 RETURNING。
6. 整洁/altitude（非阻塞）：as_task 与 convert 的前置校验三件套（archived/DM/顶级）复制两份、claim/unclaim status 联动字面量硬编码未走 TASK_TRANSITIONS、M1_TABLES/M2_TABLES 无完整性守卫。
7. ENDPOINTS_M2 登记 12 条但真 server 现 serve 8 条（files/search/activity 属 C4 第二半，符合切分）；C4 落地时补"目录 vs 实 serve"一致性测试。

## 当前状态

| 项 | 状态 |
| --- | --- |
| 仓库 | `D:\Project4work\Agenthub_7_8\coagentia` |
| 分支 | `main` |
| 合并提交 | `f2c993f merge: complete M1 implementation and hardening` |
| 修复提交 | `351684a fix: harden M1 runtime and consolidate handoffs` |
| 工作树 | 干净；M1 实现与 hardening 已合并到 `main` |
| M1 核心闭环 | 既有真实两 Agent 对话/文件产出成立；本批修复实机 review 暴露的 7 个问题 |
| 自动测试 | 238 passed，2 skipped；Web 10 passed；双侧 typecheck、build、ruff 全绿 |
| 浏览器实证 | 同源 8787 与 Vite 5173 均能连接真实 Server；桌面/390px 已目检 |

详细变更、验证矩阵和证据见 [FIX-REPORT.md](../m1-review-fixes-20260709/FIX-REPORT.md)。

## 已完成

1. 真 Server 可托管 Web dist，Vite 默认代理真实 Server，mock-only 行为显式隔离。
2. Agent REST 身份改为 Computer Bearer + Agent 隶属关系双校验。
3. 中文标点 mention、未配对 Unicode 500、错误响应二次 500 已修复。
4. OAuth 隔离凭证支持 peer 选优、原子同步和一次失败 turn 自动重投。
5. 文件绑定增加数据库回滚补偿，不再丢 staging 或制造最终目录孤儿。
6. 窄屏频道抽屉和主区响应式布局完成，WS 重连 timer 清理完成。
7. 新增回归测试并将 Ruff 从 6 个错误清到全绿。

## 启动方式

### 真实开发模式

终端 1：

```powershell
uv run coagentia-server
```

终端 2：

```powershell
pnpm --filter @coagentia/web dev
```

打开 `http://127.0.0.1:5173`。Vite 将 `/api` 和 `/api/ws` 代理到 8787。

### 同源构建模式

```powershell
pnpm --filter @coagentia/web build
uv run coagentia-server
```

打开 `http://127.0.0.1:8787`。Server 在 monorepo 中自动发现 `apps/web/dist`；安装/部署到其他目录时设置 `COAGENTIA_WEB_DIST`。

### Mock 模式

Mock 不再是默认路径。需要时显式设置：

```powershell
$env:VITE_API_BASE='http://127.0.0.1:8642'
$env:VITE_MOCK_MODE='true'
pnpm --filter @coagentia/web dev
```

## 下一步任务

### P0：实机补充验证

1. 在已登录 Claude 的干净机器状态下再跑一次双 Agent 并发冷启动，重点观察真实 OAuth refresh 竞争后的自动重投；当前已有确定性单测和凭证 peer 自愈实测，但未在本批重新消耗完整双 Agent 对话。

### P1：静态类型债务

`uv run pyright` 仍有 109 个既有错误。建议单独开批处理，不与业务修复混在一起：

1. 为 SQLAlchemy `Model.__table__` 统一提供 `Table` 类型 helper/cast，消除大部分 `FromClause` DML 报错。
2. 对 `.mappings().first()` 建立非空窄化 helper，统一 `RowMapping → dict[str, Any]`。
3. 修正 `frames.py` 的 tool id 可空键、`mcp.py` stdio 文本/二进制类型和 `api.py` JsonValue 标注。
4. 把 `pyright` 加入 CI 后再宣称 Python 静态检查全绿。

### P2：产品推进

1. **已立项 M2**（owner 决策 2026-07-09）：任务书 = [M2-HANDOFF.md](M2-HANDOFF.md)；配套契约修订已完成（A v1.0.2 / B v1.1 / E v1.1，见 engineering_docs）。**两块执行**（owner 确认切分）：块 M2a 任务闭环先收（收口即 PRD M2 出口），块 M2b 发现与聚合面随后。开工第一步 = C0 契约登记 + C1 建表。
2. 前端附件展示已并入 M2 任务书 B-M2-3（文件页签同批补消息内附件卡）；批 6 四屏为伴随任务不进 M2 出口门。
3. 更新根级 `M1-DEV-PLAN.md` 的测试数字；阶段性结论统一沉淀到本目录的 `PROJECT-RECORD.md`，避免多份状态文档继续漂移。

## 注意事项

- 若机器级和所有 Agent OAuth 凭证都已失效，自动选优没有可用来源，必须先执行一次 Claude 登录。
- mock 模式必须显式开启，否则任务查询返回本地空数组，不会向 M1 Server 请求不存在的 `/api/tasks`。
- 文件绑定现在具备请求级补偿；极端的“DB 已提交但进程在 sidecar 删除前崩溃”只会留下 sidecar，不会丢正文或 files 行。
- 实机验证生成的服务、Vite、浏览器和 Claude 进程均应在会话结束前关闭；本批验证端口为 5173/8787/8788。
