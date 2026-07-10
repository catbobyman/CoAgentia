# M2 执行计划（M2-DEV-PLAN）

> 体例同 [M1-DEV-PLAN.md](../../../M1-DEV-PLAN.md)。任务书入口 = [M2-HANDOFF.md](archive/M2-HANDOFF.md)（已随 M2 收口归档）。
> 建立于 M2 前半（C0+C1+C2 收口，`ba73f72`）之后，覆盖**剩余全部 M2**（块 M2a 收口件 + 块 M2b）。

## 会话切分与编排

本批以**多 agent 工作流**推进（owner 指令 2026-07-09）：侦察 → 后端工作流（4 agent 并行）→ 前端工作流（数据层 + 2 UI 轨并行）→ 实机 verify（C7）→ `/code-review high`。

## 进度表

| 模块 | 块 | 内容 | 状态 |
| --- | --- | --- | --- |
| C0 | M2a | 契约登记 | ✅ 已收口（`42f20f0`） |
| C1 | M2a | 建表迁移 `0002_m2` | ✅ 已收口（`42f20f0`） |
| C2 | M2a | 任务域 8 端点 | ✅ 已收口（`42f20f0`+`ba73f72`） |
| C3a | M2a | `task #n` 解析落 message_task_refs | ✅ 后端工作流（messages.py，12 测试） |
| C3b | M2b | Activity 生成（mention + dm） | ✅ 后端工作流（DM 只生成 dm 不双写 mention） |
| C4 | M2b | 文件页签 + 搜索 + Activity 端点 | ✅ 后端工作流（4 端点 + 一致性双跑；中文 FTS 结论回写 A §10.4） |
| C5 | M2a | daemon MCP 任务工具 + send_message as_task | ✅ 后端工作流（6 工具纯透传 + 结构化透传测试） |
| C6 | M2b | usage 任务归属富化 | ✅ 后端工作流（thread_root_id→task_id 三路） |
| （纪律 7） | — | TASK_TRANSITIONS 生成到 contracts-ts | ✅ 手工补（gen 管线扩 constants.ts，前端防呆单一事实源） |
| C7 | M2a | 番茄钟端到端实机 verify | ✅ 真 HTTP 17/17 + 浏览器 UI 全流程（截图归 docs/verify） |
| B-M2-1 | M2a | P1/P5 任务面接真 | ✅ 前端工作流（opsbar/composer/深链接真） |
| B-M2-2 | M2a | P3 看板 + P11 聚合板 | ✅ 前端工作流（拖列改状态 + WS 实时移列） |
| B-M2-3 | M2b | P4 文件 + P8 成员 | ✅ 前端工作流（inline 预览 + 附件卡 + 成员表） |
| B-M2-4 | M2b | P9 Activity + P10 搜索 | ✅ 前端工作流（三过滤 + Ctrl+K 三分组 + «» 高亮） |

**M2 全量收口态（独立复验）**：
- 后端 `uv run pytest -q` = **384 passed, 3 skipped**（较基线 340/2 净增 44）；`uv run ruff check .` 全绿。
- 前端 `pnpm -F @coagentia/web`：typecheck / build 全绿，vitest **18 passed**。
- `pnpm gen` 确定性；生成物含 `TASK_TRANSITIONS` + `UNCLAIMABLE_STATUSES`（纪律 7 前端防呆单一事实源）。
- 12 条 ENDPOINTS_M2 真 server serve；一致性双跑覆盖 files/search/activity。
- **`/code-review high`（8 角度 × 验证）已跑**：10 CONFIRMED → 已修 6（见下），4 记为挂账。

**review 已修（6）**：① SearchOverlay 空前缀(`in: `/`from: `)误命中首个频道/成员 → 空片段守卫；② 全局搜索"成员"跳转对人类成员 404（agent 路由）→ 按 kind 分流（人类→/members）；③ ThreadPanel 认领钮在 done/closed 未置灰（点击必吃 422）→ 消费生成的 `UNCLAIMABLE_STATUSES` 防呆；④ wsBridge `activity.created` 只更 'all' 档致 Unread/Mentions 停留页列表滞后 → 补 patch 已挂载偏档 + 3 回归测试；⑤ AttachCard/FilesTab 文件类型 helper 复制且 CODE_EXT 漂移（同名文件图标不一致）→ 抽 `lib/fileKind.tsx` 单一源；⑥ files.py/activity.py 游标分页块重复 → 抽 `routes/_pagination.py`。

**review 挂账（4，非阻塞，MVP 低量下不触发或属既有模式债）**（2026-07-10 二轮 review 修复批处置：③附件卡数据源的 WS 失效面已修、其余收纳 [M3-HANDOFF](archive/M3-HANDOFF.md) §8——注意 M2-HANDOFF 已移 archive/）：
- ActivityScreen Unread/Mentions 徽标计数取自首页('all' ≤50)，>50 项时低计（单人 MVP 量小；需 API 提供总数或改分页聚合）。
- activity/files（及既有 messages/tasks）id 游标在 after 行离开结果集时可能重发首页（`_pagination.py` docstring 已注明，与 CURRENT-HANDOFF 待决项 3 同类，留 keyset 统一整改）。
- 消息流附件卡数据源 = 单次 useChannelFiles 首页（≤50 新文件），更旧消息的附件卡不显（需消息级 file 关联，架构性，M3 前处理）。
- P11 WorkspaceBoardScreen 与 P3 BoardTab 各自实现看板列/拖拽/边表消费（轻度重复，可抽共享 <TaskBoard>，非出口门）。

## 测试策略

- 后端：`uv run pytest -q`（全量）、`uv run ruff check .`；C4 一致性套件双跑扩 files/search/activity；C5 StubHttp 往返 + 冒烟扩 1 条。
- 前端：`pnpm -F @coagentia/web test`（vitest）、`pnpm -F @coagentia/web typecheck`、`pnpm -F @coagentia/web build`。
- 生成物：`pnpm gen` diff 为空守门（契约冻结）。
- 实机：C7 番茄钟全流程 + B 线 playwright 对照归档稿，截图归 `docs/verify/`。

## 验收映射

出口清单见 [M2-HANDOFF.md](M2-HANDOFF.md) §9（9a 块 M2a / 9b 块 M2b）。
