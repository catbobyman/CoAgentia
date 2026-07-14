# 前端死壳与断链排查登记（M8-DEADSHELL-AUDIT）

| 项 | 内容 |
| --- | --- |
| 版本 / 日期 | v1.0 / 2026-07-14（M8 立项会话顺产；两轮全前端扫描 + 抽查复核） |
| 定位 | 登记"看起来可用但没接线"的 UI 死壳与前后端断链。**纯登记，未进 M8 范围**（owner 暂缓处置）；处置时对照 [M8-HANDOFF.md](project-handoffs/M8-HANDOFF.md) B-M8-3（已收录：建 Agent 入口/首跑按钮/新建频道，勿重复）。 |
| 复核状态 | A1（lifecycle 前端零引用）与 C1（setReadPosition 只有定义零调用）已主会话 grep 复核确认；其余为扫描代理结论（逐条附文件:行号，消费前建议抽查） |

## A. 完全死壳（按钮在、后端接口在、前端没线）——置信度高

| # | 位置 | 现状 | 影响 |
| --- | --- | --- | --- |
| A1 | AgentDetailScreen.tsx:82（Stop）/ :89-92（Restart·Session reset·Full reset 菜单项） | 全部无 onClick；`POST /agents/{id}/lifecycle` 在 api.ts 无函数（grep lifecycle 零命中，已复核） | **Agent 生命周期操作整体不可用**（FR-3.4 三档重置） |
| A2 | AgentDetailScreen.tsx:136/140（runtime/model selbtn） | 静态展示带 ▾ 无下拉；`PATCH /agents/{id}` 无 api.ts 函数 | 改不了 runtime/model（FR-3.5 承诺"下次启动生效"） |
| A3 | ComputersScreen.tsx:101/103（Rename/Remove） | 无 onClick；PATCH/DELETE /computers 无函数（Remove 的 disabled 防呆条件已写好） | 机器无法改名/移除（FR-2.7） |
| A4 | MembersScreen.tsx:92（发私信图标） | onClick 仅 stopPropagation；`POST /dms` 无函数 | **DM 只能看历史无法发起** |
| A5 | Rail.tsx:73/74（设置/主题切换图标） | 无 onClick；`PATCH /workspace` 无函数 | 主题/桌面通知/声音/附件上限/欢迎语全组配置无编辑入口 |
| A6 | ChannelList.tsx:79（「已归档」分组头） | 无 onClick 无展开逻辑；archive/unarchive/DELETE channel 均无函数 | 频道归档/删除链路整体缺失（FR-1.3） |
| A7 | MembersScreen.tsx:87（角色徽章） | `PATCH /members/{id}` 无函数 | 改不了成员角色（单人 MVP 影响低） |

## B. 线写好了两头没接（api.ts 有函数、无人调用）——置信度高

| # | 函数 | 影响 |
| --- | --- | --- |
| B1 | **setReadPosition（api.ts:462）** | 前端读已读、接 read.updated 广播，但**从不上报自己的已读游标 → 未读徽标与"新消息"分隔线永不清除**（已复核，最伤日常体验） |
| B2 | convertToTask（api.ts:206） | 既有消息"转为任务"无 UI（PRD FR-4.9 MVP 消息动作） |
| B3 | assignTask（api.ts:210） | 无法改派任务给指定成员（只能自己 claim/unclaim） |
| B4 | patchTask（api.ts:214） | 任务标题 / silence_override_h 无编辑面 |
| B5 | patchCanvasNode（api.ts:239） | 画布节点建后改标题/改 check 命令无入口 |
| B6 | stopPreview（api.ts:395） | 关闭预览面板只删本地面板不发 DELETE，dev server 靠 idle 超时兜底回收（可能有意，中置信度） |

## C. PRD MVP 承诺的消息动作整体缺失——置信度高

MessageFlow 无逐条消息 hover 动作菜单 → **Reply in thread / Convert to task / Copy link / Copy text 四件套全部没做**（FR-4.9 MVP 范围；普通非任务消息无线程回复入口，线程只由任务牌打开）。

## D. WS 实时缺口（契约登记事件，wsBridge 零处理，靠重连 resyncAll 兜底）

| 事件族 | 影响 | 置信度 |
| --- | --- | --- |
| computer.connected/disconnected/updated | **机器在线状态点不实时**（daemon 上下线核心信号） | 高 |
| task_contract.created/updated | 契约卡不实时（让 Agent 起草后不自动出现，taskDetail 无此 invalidate 触发） | 中高 |
| draft.adjusted/confirmed/rejected | 他方操作草稿时提案卡不实时收敛（wsBridge 只接了 draft.presented 与 delta.*） | 中 |
| channel.* 五种 / member.* 三种 | 新频道/设置/成员进出不实时 | 中 |
| agent.updated / workspace.updated | 无编辑 UI 故实际影响低 | 低 |

## E. 需与设计确认（可能有意的静态壳）

- SetupChecklistScreen 首跑屏整片聊天壳：假页签（:66-71）/ 假编辑器与假发送（:106-111）/ 顶栏⋯（:64）/「收起」（:100）/ #all 频道行（:51）——若有意，建议去掉可点样式。
- ThreadPanel.tsx:350-354 TaskPlan 摘要芯片带 ▾ 但展不开（真契约卡在下方另渲染）——去箭头或接线。
- Rail.tsx:75-78 底部头像无个人菜单。
- 低优先：Agent Home 文件不可点看内容/不能进子目录（GET home/file 无消费）、诊断无导出按钮、人类无上传附件入口（POST /files 无消费，能下载不能传；attachment_max_mb 设置项暗示本应有）、人类无建 reminder 入口（可能 Agent 专属 FR-3.9）。

## 处置（2026-07-14 更新）

修复计划已另文：**[DEADSHELL-FIX-PLAN.md](DEADSHELL-FIX-PLAN.md)**（F 系列五波编排：波1 日常体感四件[已读/生命周期/DM/主题设置] → 波3+4 管理面∥WS 补 case → 波2 消息动作四件套[含 ThreadPanel 泛化最大件] → 波5 静态壳清理；全部纯前端零契约零迁移，与 M8a/M8b 正交）。**归属**：owner 拍板 = 甲（M8 前置小批先行）+ F9 一并做。

### 逐条勾销（2026-07-14 完成，F 系列全绿收口）

全部 F1–F13 已实现并守门全绿（web vitest 403→**444**、pyright+tsc 0 错、build 绿、pnpm gen 确定、后端零改动）。真机浏览器实证（真 server+seed）：F1 read-position PUT 聚焦即发/非聚焦抑制、F2 lifecycle 503 离线路径、F3 DM POST 200+跳转、F4 主题 dark→light+PATCH+设置弹窗五项、F5 转任务 201+任务牌+卡片消息不显转任务、F5-④ 线程回复携 thread_root_id 归线程不漏主流。

| 审计项 | 修复 | 状态 |
| --- | --- | --- |
| A1 生命周期死壳 | F2（Stop/Restart/Session reset/Full reset[确认弹窗]，503 toast） | ✅ |
| A2 runtime/model 静态 | F7（真下拉：runtime=detected_runtimes 未装置灰；model 池+自由输入；description 就地编辑；下次启动生效） | ✅ |
| A3 机器 Rename/Remove | F6（就地改名 + Remove 确认弹窗；HAS_AGENTS/PROJECTS toast） | ✅ |
| A4 发私信死壳 | F3（createDm 幂等 → 跳转 DM） | ✅ |
| A5 设置/主题图标 | F4（主题翻转即时+PATCH 回滚；WorkspaceSettingsModal 五项） | ✅ |
| A6 归档分组+归档/删除 | F8（Topbar 菜单 + ChannelList 折叠组[主列排除 archived] + 归档只读 + 删除确认[CHANNEL_NOT_EMPTY toast]） | ✅ |
| A7 成员改角色 | F9（owner/admin 下拉，权限矩阵 R1/admin 门/非自身） | ✅ |
| B1 已读永不上报 | F1（useReadCursor 节流/去重/乐观/失败清标记；聚焦门） | ✅ |
| B2 convertToTask 无 UI | F5-③（hover 转任务，仅顶级频道消息、非卡片） | ✅ |
| B6 关面板不回收 | F11（关闭即 stopPreview） | ✅ |
| C 消息动作四件套 | F5（Copy text/link + 转任务 + 线程回复；ThreadPanel 泛化非任务 root；线程回复携 thread_root_id） | ✅ |
| D WS 实时缺口 | F10（computer.*/channel.*/member.* invalidate；task_contract.* → taskDetail；draft.adjusted/confirmed/rejected 仅 proposal_id→invalidate；agent.updated/workspace.updated 替换） | ✅ |
| E SetupChecklist 假壳 / ThreadPanel ▾ / Rail 头像 | F12/F13（示意态降级、去 ▾、去可点暗示） | ✅ |

未纳入（审计 §E 观察区，需求未确认，本批不做）：人类上传附件 / Home 文件查看 / 诊断导出 / 人类建 reminder；B3 assignTask / B4 patchTask / B5 patchCanvasNode（既有编辑面外的辅路，非死壳核心）。**「新建频道」「创建 Agent」按钮接线归 M8 B-M8-3**（不在本批）。
