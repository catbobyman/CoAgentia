# 前端死壳修复计划（DEADSHELL-FIX-PLAN）

| 项 | 内容 |
| --- | --- |
| 版本 / 日期 | v1.0 / 2026-07-14（问题清单 = [M8-DEADSHELL-AUDIT.md](M8-DEADSHELL-AUDIT.md) v1.0，本文只管修法与顺序） |
| 定位 | **独立可执行的修复计划**，尚未并入任何里程碑（owner 决定：作为 M8 前置小批单独跑 / 并入 M8c / 留 M9）。模块编号 **F 系列**（不占 L 系列，并入 M8 时可整体映射为 L14 或 B-M8-4） |
| 总判断 | 全部问题**后端与契约均已就绪**（六契约零修订、零新端点、零迁移），修复 = 前端接线为主；无一触及状态机/内核/并发面，与 M8a 加固批、M8b O8 线完全正交，可并行也可先行 |
| 守门 | `pnpm -F @coagentia/web test`（403 只增不减）· `pnpm typecheck` · `pnpm -F @coagentia/web build` · 关键件 Playwright 真机抽验（F1/F2/F3 必验）；后端测试不应有任何变化（改动不触后端） |

## 0. 总原则

1. **api.ts 缺的函数照既有体例补**（fetch 包装 + 错误信封解析），一律对照契约 B 已冻结形状，不发明字段。
2. **接线优先于造新 UI**：能挂到既有组件（弹窗/下拉/菜单体例）就不新建组件形态。
3. **每件修复带两样东西**：行为测试（vitest）+ 真机可见验证（截图或 Playwright）——"接上了"以用户能看到为准。
4. 不可撤销动作（Full reset、删频道、移除机器）**必须确认弹窗**（表单防呆先于报错，交互原则 §7.3）。
5. 本计划**不做**审计 §E 里"需求未确认"的可选件（人类上传附件/Home 文件查看/诊断导出/人类建 reminder）——留在审计文档观察区，要做另议。

## 波 1 · 日常体感件（最先修，每一件都是"每次用都碰到"）

| # | 修复 | 修法要点 | 验收 | 规模 |
| --- | --- | --- | --- | --- |
| F1 | **已读游标上报**（未读永不清） | 调用已存在的 `api.setReadPosition`（api.ts:462）：ChannelChatScreen 在「频道可见 && 消息流滚动到底 / 新消息到达且窗口聚焦」时上报最新消息 id；切频道时对上一频道补报；**节流**（如 2s）防连发；乐观更新本地 read_positions（wsBridge 已接 read.updated 回流兜底，含忽略 agent 游标的既有守卫） | 打开频道看完消息 → 侧栏未读徽标清零、"新消息"分隔线消失；他端同步；后台频道不误报 | S |
| F2 | **Agent 生命周期接线**（Stop/Restart/Session reset/Full reset） | api.ts 补 `agentLifecycle(id, action)`（`POST /agents/{id}/lifecycle`，action 枚举照契约 B LifecycleRequest 冻结形状）；AgentDetailScreen.tsx:82 Stop 接线；:89-92 下拉三项接线；**Full reset 走确认弹窗**（不可撤销，红色确认 + 输入 Agent 名防呆可选）；操作后 invalidate agent 详情（presence 变化 wsBridge agent.status 已接） | 真机：Stop → 状态点变灰；Restart → 走 Starting→Idle；Session reset 保 Home；Full reset 弹确认；错误信封（daemon 离线 503）toast 显示 | S |
| F3 | **发私信**（DM 只能看不能建） | api.ts 补 `createDm(memberId)`（`POST /dms`，幂等：已存在同对 DM 返既有频道——以契约 B 语义为准）；MembersScreen.tsx:92 图标接线 → 建/取回 DM → 路由跳转该 DM 频道；ChannelList DM 分组随 channels invalidate 出现 | 真机：成员页点发私信 → 跳进 DM 能发消息；重复点不建重复 DM | S |
| F4 | **主题切换 + 工作区设置** | api.ts 补 `patchWorkspace(patch)`；Rail.tsx:74 主题图标 → 翻转 ui_theme（本地立即切 CSS 主题类 + PATCH 落库，失败回滚）；Rail.tsx:73 设置图标 → 新建轻量设置弹窗（工作区五项：ui_theme/notif_desktop/notif_sound/attachment_max_mb/onboarding_greeting，照 ChannelSettingsModal 体例）；wsBridge 补 `workspace.updated` case（invalidate workspace） | 真机：点主题图标深浅切换且刷新后保持；设置弹窗改桌面通知开关生效（desktopNotify.ts 读的就是这组配置） | M |

## 波 2 · 消息动作四件套（PRD FR-4.9 MVP 承诺）

| # | 修复 | 修法要点 | 验收 | 规模 |
| --- | --- | --- | --- | --- |
| F5 | **逐条消息 hover 动作菜单** | MessageFlow 加 hover 动作条组件（照 Slack 体例，右上浮出，≤4 图标）：① **Copy text**（剪贴板，纯前端）；② **Copy link**（深链 `?thread=<root>&msg=…` 统一格式——**依赖 M8a B-M8-1 深链修复**，若本批先行则 Copy link 生成的链接暂以"能还原频道+线程"为准）；③ **Convert to task**（调既有 `api.convertToTask`，仅顶级频道消息显示——T3/DM 不承载任务，成功后消息处出现任务牌）；④ **Reply in thread**（打开线程面板：ThreadPanel 现为任务中心，需泛化支持"普通消息为 root"的线程视图——渲染回复流+回复框，任务专属区（状态流转/契约卡）按 root 是否任务条件渲染） | 四动作各行为测试；真机：非任务消息开线程回复、回复不进主流（PRD §4.1）；转任务后编号正确；复制链接可还原 | **M+**（④ 是本计划最大单件——ThreadPanel 泛化） |

## 波 3 · 管理面成组接线

| # | 修复 | 修法要点 | 验收 | 规模 |
| --- | --- | --- | --- | --- |
| F6 | 机器 Rename / Remove | api.ts 补 patchComputer/deleteComputer；Rename → 就地输入或小弹窗；Remove → 确认弹窗（既有 disabled 防呆保留；服务端 COMPUTER_HAS_AGENTS 422 兜底 toast） | 改名实时反映；有 Agent 时不可删、删光后可删 | S |
| F7 | Agent runtime/model/description 编辑 | api.ts 补 patchAgent；AgentDetailScreen 两个 selbtn 接真下拉（runtime 选项 = 该机 detected_runtimes，未装置灰 "(not installed)" FR-2.3；model 列表沿创建弹窗数据源）；"下次启动生效"提示保留 | 改 runtime/model 落库；置灰逻辑正确；重启后生效（配 F2 真机验证） | M |
| F8 | 频道归档 / 取消归档 / 删除 | api.ts 补 archiveChannel/unarchiveChannel/deleteChannel；入口 = Topbar 频道设置菜单（既有下拉体例加两项）+ ChannelList「已归档」分组头接展开逻辑并渲染 archived 频道（只读进入可浏览，FR-1.3 冻结语义）；删除走确认弹窗；wsBridge 补 channel.* case | 归档后频道入折叠组、可读不可发；取消归档恢复；删除需确认；实时广播他端同步 | M |
| F9 | 成员改角色（可选，单人 MVP 影响低） | api.ts 补 patchMember；MembersScreen 角色徽章 → owner 可改下拉（权限矩阵 §3.1：admin 仅 Member 级）；**默认建议本批不做**（无第二人类，改了也没观众）——owner 点头才进 | （若做）改角色落库 + 权限矩阵红例 | S |
| F11 | 关闭预览面板即回收 | PreviewPanel 关闭按钮在 closePreview 本地移除之外调既有 `api.stopPreview`（失败静默——idle 回收兜底仍在，语义：人关面板=人不看了，回收 dev server 省资源；与 M7 裁决 #11"人开的面板人再点"对称） | 关面板 → preview_sessions 行走 stop、daemon 杀 dev server；失败不打扰用户 | S |

## 波 4 · WS 实时缺口补齐（wsBridge 补 case，全部照既有 case 体例 = invalidate 对应 query 或缓存精确更新）

| # | 事件族 | 处理 | 规模 |
| --- | --- | --- | --- |
| F10a | computer.connected / disconnected / updated | invalidate computers + agents（在线点实时变灰/变绿——daemon 上下线核心信号） | S |
| F10b | task_contract.created / updated | invalidate 对应 taskDetail（契约卡实时出现——"让 @Agent 起草"闭环） | S |
| F10c | draft.adjusted / confirmed / rejected | 并入既有 draft.presented/delta.* 分支（提案卡/草稿层实时收敛） | S |
| F10d | channel.*（五种）/ member.*（三种） | invalidate channels / members（F8/F3 的实时面配套） | S |
| — | agent.updated / workspace.updated | 随 F7 / F4 一并接（编辑面上线后才有意义） | 并入 |

## 波 5 · 静态壳清理与小件

| # | 修复 | 修法要点 | 规模 |
| --- | --- | --- | --- |
| F12 | 首跑屏静态壳降级 | SetupChecklistScreen 的假页签/假编辑器/假发送/顶栏⋯/「收起」：**样式降级为明确的示意态**（去 cursor:pointer、加低饱和度/禁用态），或删除假编辑器直接放真 Composer——取其一（建议前者，首跑屏定位是引导示意）；「新建频道」「创建 Agent」两个按钮的接线归 M8 B-M8-3 不在本批 | S |
| F13 | 零碎三件 | ① ThreadPanel.tsx:350 TaskPlan 摘要芯片：去 ▾ 箭头（真契约卡在下方已有）；② ChannelList「已归档」分组头随 F8 接线（不单列）；③ Rail 底部头像：本批不做菜单（无个人设置项可挂），去 hover 可点暗示 | S |

## 执行编排

```
波 1（F1→F2→F3→F4，可两两并行）        ≈ 1 个工作单元，收完日常体感立变
  → 波 3 + 波 4 并行（管理面接线 ∥ wsBridge 补 case，文件域不相交）
  → 波 2（F5 最大件，ThreadPanel 泛化单独做，前面的动作菜单壳可先行）
  → 波 5 收尾清理
  → 收口：vitest/typecheck/build 全绿 + Playwright 三验（未读清除/生命周期/DM）+ 截图归档
```

- **依赖注记**：F5-② Copy link 依赖 M8a 深链修复（B-M8-1）才算完整——若本批先于 M8a 跑，Copy link 可后置到波 5；其余各件零依赖。
- **与 M8 的关系（owner 拍板位）**：方案甲 = 本计划作为 **M8 前置小批**先行单独收口（体感立改，M8 开工时基线更干净）；方案乙 = 整体并入 **M8c**（映射 L14/B-M8-4，随外壳一起收）；方案丙 = F9 之外全做、F9 留 M9。推荐**甲**——纯前端、零契约、与 M8a/M8b 完全正交，先修先受益。

## 出口清单

- [ ] 1. F1：看完消息未读清零、分隔线消失、他端同步、节流生效
- [ ] 2. F2：四个生命周期动作真机各一遍；Full reset 确认弹窗；离线 503 toast
- [ ] 3. F3：发起 DM 跳转可聊；幂等不重复建
- [ ] 4. F4：主题切换持久生效；设置弹窗五项可改可生效
- [ ] 5. F5：四动作可用；非任务消息线程回复不进主流；DM 内无"转任务"
- [ ] 6. F6/F7/F8（F9 视拍板）：管理面逐件真机过；置灰/防呆/确认弹窗齐
- [ ] 7. F10：拔 daemon 网线在线点实时变灰；起草契约卡实时出现；他端草稿操作实时收敛
- [ ] 8. F11：关预览面板 dev server 被回收
- [ ] 9. F12/F13：首跑屏无"看着能点其实不能"的元素残留
- [ ] 10. 守门全绿 + Playwright 三验 + 截图归档；AUDIT 文档逐条勾销、CURRENT-HANDOFF 挂账行更新
