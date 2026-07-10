# CoAgentia 会话交接(HANDOFF)

> 更新:2026-07-09,品牌改名 CoAgentia + 工程线契约 A 冻结。
> 用途:上下文 compact 后恢复工作状态的唯一权威。**每次批次验收后更新本文件。**

## 品牌改名(2026-07-09,owner 决策:AgentHub → CoAgentia)

- **已完成**:全部 .md 文档与技术标识(`coagentia-daemon`、`coagentia.task-plan.v1` 等 schema 版本号、`coagentia_server` 包名、`~/.coagentia/`)、PRD 文件名(→ CoAgentia-PRD.md)、竞品研究中的本项目提及。
- **待收尾(目录被进程占用,本会话内改不了)**:`docx_agenthub/ → docx_coagentia/`、项目根 `Agenthub_7_8 → CoAgentia_7_8`。**关闭本会话后双击 `D:\Project4work\finish-rename-coagentia.cmd` 完成**;在此之前文档内的 `docx_coagentia/` 路径链接暂时指不到实体目录(已知状态,勿"修复"回旧名)。
- **设计线资产仍为 AgentHub 品牌,刻意未动**:afterglow-ds 镜像全部 HTML/build.py(同步纪律)、校准样张、像素 logo(字母 A)、wordmark、boot 叙事文案;远端两个 claude.ai/design 项目名(下节,保持原文引用)。**设计品牌改名 = 独立设计线任务**(重定 logo 字母、改 wordmark/boot 文案、re-verify 后推回远端),待 owner 发起。

## 项目一句话

CoAgentia:契约驱动、流程可编排、护栏可干预的多 Agent 协作平台(IM 心智 + daemon + BYO CLI runtime)。当前阶段:**纯文档 + 设计稿,无产品代码**。两条线:设计线(用户在 claude.ai/design 画高保真,我 verify+归档)+ 工程线(M1 技术契约,**未开工**,邀约挂着)。

## 恢复上下文:按需读这些文件

| 场景 | 读什么 |
| --- | --- |
| 全局产品需求 | [docx_coagentia/CoAgentia-PRD.md](../../../../docx_agenthub/CoAgentia-PRD.md)(v1.4;附录 B 末行有第五轮全部决策溯源) |
| 设计/verify 工作(最常用) | [设计规范.md](../../../../docx_agenthub/03-设计文档/设计规范.md)(v2.1,token 宪法+元素库白名单)→ [原型图说明.md](../../../../docx_agenthub/03-设计文档/原型图说明.md)(v1.2,P0–P15 全部界面)→ [UI设计说明.md](../../../../docx_agenthub/03-设计文档/UI设计说明.md)(v1.2)→ [交互说明.md](../../../../docx_agenthub/03-设计文档/交互说明.md)(v1.2) |
| Orchestrator / M6 | [orchestrator_docs/](../../../../orchestrator_docs/README.md):拆解设计 v1.1(**已迁入此目录**;delta 部分接受语义在 §11)+ 可行性评估/实现难点/接入架构三份评审文档(2026-07-08,含 2 处设计缺陷待修与 M1 契约预留清单) |
| 某机制"为什么这样定" | [借鉴与原创对照清单.md](../../../../docx_agenthub/借鉴与原创对照清单.md)(v1.2) |
| 组件库与已出设计稿 | `docx_coagentia/04-设计稿/afterglow-ds/`(previews/ 已归档屏;build.py 可再生 13 张组件卡;tokens/ 唯一 token 源) |
| 竞品参考(慎用) | `docx/`(Raft 研究;**红线:严禁把 Neobrutalism 风格带进设计**) |

## 设计系统:Afterglow(余辉)

- 风格学名 Terminal Bauhaus;命名与由来已录入设计规范头表。
- claude.ai/design 项目:**"Afterglow — AgentHub Design System"**,projectId `f8708be9-b8f6-4472-b3eb-f46078e0c4ff`(DesignSync 可读写;含 previews/tokens/docs/fonts)。
- 同账号旧项目 "AgentHub Terminal Bauhaus"(projectId `0b6d2bba-3689-4023-bf69-179b27bf3808`)= 另一来源的 React 组件库,**未动、勿覆盖**;用户未表态去留。
- Departure Mono 字体:远端已上传(fonts/DepartureMono-Regular.woff2);本地在 `04-设计稿/fonts/DepartureMono-1.500/`(OFL)。
- **同步纪律:`afterglow-ds/` 目录结构 = 远端项目结构的逐字节镜像**(previews 内 `../tokens/afterglow-tokens.css` 相对引用依赖此结构);本地是事实源,远端改动先拉回归档再推回。

## 批次安排与状态(共 7 批,勿改动划分)

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| 0 | 校准样张(`04-设计稿/afterglow-batch0-calibration.html`)→ 已升格为设计系统 | ✅ |
| 1 | P1 会话 + P5 线程面板 | ✅ 验收归档 |
| 2 | P0a boot / P0b 创建工作区 / P0c 起步清单 / P6 Agent 详情 / P7 机器 | ✅ 验收归档 |
| 3 | P3 看板 / P4 文件 / P8 成员(同屏含 P15 资料卡)/ P9 Activity / P10 搜索 / P11 聚合板 | ✅ 验收归档 |
| 4 | P2 画布 ×5 态:正式 / 草稿 / delta / blocked / 缩放降级(P2a–P2e,一屏一态) | ✅ 验收归档 |
| 5 | 卡片族特写:HeldDraft / Diff / 部署 / fail-closed / 提案卡(C1–C5,各含状态变体) | ✅ 验收归档 |
| 6 | P12 设置 / P13 弹窗集(→ 拆为 P13a/P13b)/ P14 私信 / P15 成员面板(独立版) | ✅ 验收归档 —— **全 7 批完成** |

- **M1 开工只依赖批次 1+2(已齐)**;批次 3+ 可与开发并行,进度跑在对应里程碑(3→M2、4→M3、5→M4/M6-7、6→M2+/M5)前面即可。
- 批次 6 已验收(multi-agent 并行审 + 串行渲染实证):P14/P15 零改;P12 修 logo(像素 A 画错)+ 补 ops-private/已归档 侧栏 + `.ch .lock`/`.arch` CSS + danger 墨色;P13 修 `max_min→max_runtime_min` + danger 墨色。四屏均 playwright 1440×900 截图实证。
- **新 token `--on-danger:#FFFFFF`(两主题同值)**:系统原有 `--on-accent`(深墨,给浅橙 accent)却无 danger 实底墨色,13 张组件卡 + P12/P13 因此硬编码 `#FFF`(违"零发明")。已补入 tokens.css + 规范 §2.7 + build.py(三处同步),P12/P13 改用 `var(--on-danger)`。**build.py 曾漏 `--blink`/`--scrim`(批次 3 只改了 tokens.css),再跑会回退——本次一并补齐**。13 张组件卡的 `#FFF` 为遗留债,下次 `python build.py` 自动清理(本次未重生成,本地/远端仍一致)。
- **P13 已按 owner 决定拆成两张卡**(原单张 4 弹窗 2×2 在 1440×900 内纵向溢出 ~250px、底行被裁):`P13a-modals-create-template.html`(创建 Agent + 存为模板)/ `P13b-modals-contract-danger.html`(LoopContract + 危险确认),各 2-up 单行完整入框,已删除旧 `P13-modals.html`(远端同步)。P13b budget 从双列改**堆叠单列**——长参数名 `max_runtime_min` 在半宽格里会被 modal `overflow:hidden` 裁掉,堆叠后整行宽度可完整显示(已 DOM 实测 `clippedByModal:false` + 截图确认)。
- 批次 5 卡片特写(C1–C5)是**特写样张**(无 rail/侧栏/画布边),规避了全部复发坑,质量最高;唯一修正 C2 diff 行底纹 rgba→`color-mix(var(--success/danger) 8%,transparent)`(token 派生,守"零发明")。
- 批次 3 验收时的 token 补漏(已推回远端):tokens.css 曾漏定义 `--blink`(规范 §2.7 本有,导致 P1/P5/P10 终端光标不闪),已补齐;模态遮罩新增 `--scrim`(规范 §2.7 + tokens 两处同增,深 rgba(4,5,4,.6)/浅 rgba(25,26,23,.4))。P10 遮罩已改用 `var(--scrim)`。

## verify SOP(每批固定流程)

1. `DesignSync list_files`(projectId 见上)→ 找新增/变更的 `previews/*.html`。
2. `get_file` 逐张拉取 → **代码级审查**(重点:枚举值、产品语义文案、屏间一致性、token 零发明)。
3. 本地归档:Write 到 `04-设计稿/afterglow-ds/previews/` 同名文件,**修正随写**。
4. 渲染验收:`cd docx_coagentia && python -m http.server 8734 --bind 127.0.0.1`(run_in_background,别在命令里加 `&`,否则 TaskStop 杀不掉)+ playwright 打开 `http://127.0.0.1:8734/04-%E8%AE%BE%E8%AE%A1%E7%A8%BF/afterglow-ds/previews/<file>`(注意目录名要 URL 编码)+ 截图 Read 目检。
5. 有修正 → `finalize_plan`(writes=该批文件清单、deletes=[]、localDir=`D:\Project4work\CoAgentia_7_8\docx_coagentia`)→ `write_files`(localPath=`04-设计稿/afterglow-ds/previews/...`)推回,保持三方一致。
6. 清理:TaskStop 服务器(必要时按端口 netstat 找 PID taskkill)、删项目根的临时 `*.jpeg`。
7. 汇报:结论表 + 修正清单 + 下一批 prompts。

## 复发性修正点(verify 时先查这五条)

1. rail 像素 logo 必须是像素 **A**:4×4 格 `·██· / █··█ / ████ / █··█`(--accent)——Claude Design 反复画错。
2. `verify_by` 枚举仅 `command | inspect | manual`(PRD §4.3),出现过 "review"。
3. Runtime/model 修改 = **下次启动生效**(FR-3.5),不是"保存即热生效"。
4. 侧栏一致性:频道列表须含 ops-private(🔒)与"已归档"折叠分组(P0c 首跑态例外:只有 #all)。
5. token 零发明:只许用 `tokens/afterglow-tokens.css` 变量;Lucide 走 unpkg CDN 可接受(实现期本地打包)。
6. 画布连线 SVG 陷阱(批次 4 起):节点按 px 绝对定位,连线 SVG **不要**用 `viewBox="0 0 1000 620" preserveAspectRatio="none"`(会把坐标拉伸到实际视口 ~1150×775,箭头与节点错位)。直接 `<svg class="edges">` 无 viewBox,路径用 px 坐标即与节点对齐。悬浮工具条/确认条居中用 `left:0;right:0;margin:0 auto;width:max-content`,勿用 `left:50%;translateX(-50%)`(绝对定位 shrink-to-fit 会被限制在右半屏、挤压按钮换行)。

## 工程线(已开工:技术文档阶段)

- **技术选型已定稿**(2026-07-09,owner 两轮拍板,v1.1):[engineering_docs/00-技术选型.md](../../../../engineering_docs/00-技术选型.md)——**后端与 daemon = Python**(FastAPI / SQLite+SQLAlchemy 2.0+Alembic / Starlette WS / uv,daemon 走 `uvx`),**前端 = TS**(React 19+Vite 从静态屏重搭,旧组件库不做基座 / React Flow / TanStack),**契约 = Pydantic-first**(单向生成 TS 侧;03 §3.3 同构承诺降级为"同一 schema 源+双实现+金标向量",owner 知悉)。工程线文档归集处 = [engineering_docs/](../../../../engineering_docs/README.md)(契约 A–E 编号 01–05 已预留)。
- M1 技术契约进行中:**契约 A(实体表)与契约 C(WS 事件协议)已冻结 v1.0**([01-实体表与数据模型.md](../../../../engineering_docs/01-实体表与数据模型.md):31 表、指纹序列化规范、账本 batch_id 修订、画布基线语义;[03-WS事件协议.md](../../../../engineering_docs/03-WS事件协议.md):信封四要素、30+ 事件目录、断线恢复=REST 重同步(无 outbox,裁决了 A §10.1)、draft/delta 一名两用)。**契约 B(REST)也已冻结 v1.0**([02-REST-API契约.md](../../../../engineering_docs/02-REST-API契约.md):三主体身份模型、权限编码、19 错误码、13 域端点、S2 CAS、幂等重试复用账本)。**契约 D(daemon↔server)已冻结 v1.0**([04-daemon-server协议.md](../../../../engineering_docs/04-daemon-server协议.md):接入认证/心跳、五 kind 帧模型、握手对账=无指令 outbox 而是 DB 事实源推导补发、指令/查询/上报三目录含 S1 直投与 S4 幂等消费、投递语义与已读游标、数据目录布局——裁决了 A §10.2 与 B §8.1 孤儿文件 GC)。**契约 E(Claude Code 适配器)已冻结 v1.0**([05-ClaudeCode适配器进程模型.md](../../../../engineering_docs/05-ClaudeCode适配器进程模型.md):命令行与 CLAUDE_CONFIG_DIR 配置隔离、coagentia MCP 行为通道(Agent 行为唯一出口)、会话簿记与三档重置映射、崩溃熔断、activity 相位聚合——裁决了 C §10.1 不新增工具级事件、usage 提取与任务归属富化、RuntimeAdapter 接口与 Codex M5 扩展位)。**五份契约 A–E 全部冻结**(A/B 后升 v1.0.1 编辑修正:表数 34、错误码 20——contracts 对照测试核出的计数笔误,无形状变更)。**M1 契约任务已收口**(2026-07-09):代码仓 [coagentia/](../../../README.md)(项目根下,独立 git)落地 `packages/contracts`(Pydantic 唯一源,manifest 对照测试把五份契约逐名钉死)+ fixtures(设计稿同款,含指纹金标判例)+ mock server(REST M1 端点 + WS 信封广播 + 时间线回放)+ TS 生成管线(重跑 diff 为空守门)+ **P1 会话屏形状验证**(apps/web,零手写实体类型,playwright 截图实证于 coagentia/docs/verify/:静载渲染 + 时间线回放五事件无刷新更新,NFR1 首次实证)。下一步:M1 实现阶段,**任务书 = [M1-IMPL-HANDOFF.md](M1-IMPL-HANDOFF.md)**(线 A 后端 A1–A8 / 线 B 前端 B1–B4、每模块 DoD、出口验收清单)。
- → **任务书**:[M1-HANDOFF.md](M1-HANDOFF.md)(5 份契约 + `packages/contracts` + mock 的交付清单、03 §6 的 6 条 M1 预留项、已拍板约束、事实源)。其 §8 开放问题已全部拍板(2026-07-09):技术栈见选型文档;**契约+mock 先行**,15 屏框架化为独立工作流随 M1 实现阶段启动。

## 关键决策速查(已拍板,勿再问)

单人 MVP(邀请人类后置,非目标 #11)· delta 部分接受(语义定稿于拆解设计 §11)· 技能白名单创建后配置(P6 技能 tab 唯一入口)· DM 不承载任务(FR-5.1)· 深链 `?tab=canvas&node=` · 全页搜索后置(非目标 #12)· bypassPermissions + api-key 命令行明文 = owner 刻意决策 · 任务状态写权限全员开放(D2 不做)· 视觉红线:禁渐变/玻璃拟态/卡片投影/圆角>4px/Raft Neobrutalism 组合特征。
> 归档说明：本文是设计与品牌阶段的历史快照，内容不再更新。阶段结论见 [项目记录](../PROJECT-RECORD.md)，当前状态见 [当前交接](../CURRENT-HANDOFF.md)。
