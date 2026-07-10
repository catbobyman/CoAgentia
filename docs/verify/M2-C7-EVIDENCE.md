# M2 C7 实机 verify 证据（番茄钟全流程 + B 线全屏）

> 2026-07-10。隔离验证服务 = 临时 DB（alembic upgrade head + seed）+ uvicorn on 127.0.0.1:8799，
> 同源托管 `apps/web/dist`（真实 build 产物）。人 = 浏览器 owner（Memcyo）；Agent 动作持
> Computer api-key + `X-Acting-Member` 发 REST（模拟 daemon MCP 代理），驱动 WS 无刷新更新。

## 1. 番茄钟后端全流程（真 HTTP + WS）—— 17/17 PASS

脚本 `scratchpad/c7_pomodoro.py`（独立 8799 实例）逐步断言全绿：

| 步骤 | 断言 |
| --- | --- |
| 人发 As Task | 201 原子 `{message, task}`；task 起始 todo / 无 owner |
| Agent list_tasks | 200 看见任务 |
| Agent claim | 200 → in_progress（todo→in_progress 联动）+ owner=agent |
| Agent 线程汇报 | 201 |
| Agent upload_file + 交付绑定线程 | 201 + 201 |
| Agent set_task_status in_review | 200 → in_review |
| 人置 done | 200 → done |
| TaskDetail | done + usage 聚合形状 |
| 文件页签 | GET /channels/{id}/files 见交付文件 |
| done 终态守卫 | 422 TASK_TRANSITION_INVALID |
| task #n 引用消息 | 201（服务端解析落 refs） |
| WS 广播 | task.created / task.updated / message.created 全部触发（无刷新数据流） |

## 2. B 线全屏浏览器实证（真 build dist，同源 8799）

截图归档本目录：

| 屏 | 截图 | 要点 |
| --- | --- | --- |
| P1 会话 | [m2-p1-channel-real.png](m2-p1-channel-real.png) | 真实频道/消息/成员/composer |
| As Task 无刷新 | [m2-astask-taskchip-live.png](m2-astask-taskchip-live.png) | 发 As Task → 消息内 `#8 Todo` 任务牌即时渲染（WS，无刷新） |
| P3 看板 | [m2-p3-board.png](m2-p3-board.png) | 5 态分列、Done/Closed 折叠窄条、#8 在 Todo |
| **P3 claim 无刷新移列** | [m2-p3-board-after-claim-ws.png](m2-p3-board-after-claim-ws.png) | Agent REST claim → #8 从 Todo(0) 实时移到 In Progress(1)，无刷新（WS task.updated） |
| P11 聚合板 | [m2-p11-aggregate-board.png](m2-p11-aggregate-board.png) | Board/List 双视图、Channel/Creator/Assignee 过滤、跨频道聚合、owner=Momo |
| P9 Activity | [m2-p9-activity.png](m2-p9-activity.png) | All/Unread(2)/Mentions(1) tabs、mention+dm 行、Mark as done（点击后 Unread 2→1、Mentions 1→0 实时） |
| P10 搜索（任务） | [m2-p10-search-task.png](m2-p10-search-task.png) | Ctrl+K 覆盖层、"番茄" → 任务组 #8（title LIKE 中文命中） |
| **P10 搜索（FTS 高亮）** | [m2-p10-search-fts-highlight.png](m2-p10-search-fts-highlight.png) | "Hank" → 跳转组(成员) + 消息组(FTS 命中 `«»`→`<mark>` 高亮) |
| P8 成员 | [m2-p8-members.png](m2-p8-members.png) | HUMANS/AGENTS 分段、角色徽章、runtime(Codex/Claude Code)、在线态 |
| P5 线程操作条 | [m2-p5-thread-opsbar.png](m2-p5-thread-opsbar.png) | 任务牌头 + 契约卡「暂无契约(M3)」占位 + 认领/流转操作条激活 |
| **P5 合法边防呆** | [m2-p5-status-legal-transitions.png](m2-p5-status-legal-transitions.png) | in_progress 下拉**只列 Closed/In Review/Todo**（TASK_TRANSITIONS 单一事实源，无 done/自身） |
| P5 流转无刷新 | [m2-c7-p5-in-review.png](m2-c7-p5-in-review.png) | 选 In Review → 面板 + 消息流任务牌**同步**变 In Review（WS 跨组件一致） |

## 3. 关键结论

- **番茄钟出口达成**：人发任务 → Agent 认领（联动）→ 线程汇报 → 文件交付 → in_review → 人置 done，全程真 HTTP + 真 UI，WS 驱动无刷新。
- **纪律 7 防呆闭环实证**：前端流转下拉与看板拖列消费**生成的** `TASK_TRANSITIONS`（contracts-ts `constants.ts`，源 = Python `constants.py`），in_progress 只放行三条合法边，done 不可选。
- **中文检索结论**（写回契约 A §10.4）：unicode61 对 CJK 子串 FTS 不命中（整串单 token），jumps/tasks 走 LIKE 中文可用；消息正文 FTS 换 trigram 归 M3。
