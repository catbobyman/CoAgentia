# M8 前端输入面审计（手填盲选同族问题登记）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-14，owner 确认「CreateAgentModal 模型字段应消费 probe 检测的模型列表做候选」后，全前端同族排查 |
| 判定标准 | ① 自由文本手填但系统已有权威可枚举数据源未消费；② 选择器未消费「已检测/已安装」信息做置灰过滤（能选到必然失败的项）；③ 填错只在提交后/运行期才暴露、无即时前端校验 |
| 排查范围 | apps/web/src 全部 modal/form/settings 组件逐 input/select 核对，权威数据源经 contracts-ts 生成物与 data/queries 层 grep 实证 |
| 结论 | 同族问题 5 处（HIGH×2 / MEDIUM×1 / LOW×1 / MINOR×1）；聚集在 **CreateAgentModal（两处未消费 probe）** 与 **Project 编辑器（三处运行期才暴露）** |

## 确认清单（按严重度）

### 1. [HIGH] CreateAgentModal 模型字段自由文本（owner 已确认立项）

`CreateAgentModal.tsx:131`。`model` 必填手填，原样透传 runtime（claude `--model` / codex JSON-RPC）。**权威源已可取**：同组件已 `useComputers()`，`computers[].detected_runtimes[runtime].models` 就在手边；`AgentDetailScreen.tsx` ProfileTab（332-336）已有「候选池 + 自由输入兜底」参考实现。填错暴露时机 = CLI 启动/运行期。

**修法**：抄 ProfileTab 体例——按所选机器+runtime 出模型候选（datalist 或下拉+自由输入兜底），零契约零后端。

### 2. [HIGH] CreateAgentModal Runtime 分段不消费 probe.installed

`CreateAgentModal.tsx:116-124`。硬编码 `['claude_code','codex']` 分段：①不据 `detected_runtimes[].installed` 置灰——能选到未安装 runtime，启动必失败才暴露；②探测到的其他 runtime（如 gemini）根本选不到。`AgentDetailScreen.tsx:300-311` 是现成正解（"(not installed)" 置灰）。

**附带布局问题**：模型候选依赖「机器+runtime」二元组，但「所在机器」选择器（:141）排在「模型」（:131）之后——修 #1 时应把机器上移到 runtime 旁。

### 3. [MEDIUM] Project 仓库路径无存在性校验

`ProjectSettingsSection.tsx:170`。`repo_path` 自由文本，无枚举源也无存在性校验，路径错误只在 worktree.ensure / deploy 运行期暴露。**修法需要新端点**（daemon/server 侧路径探测），非纯前端——单独裁决。

### 4. [LOW-MED] Project 部署/开发命令无前端校验

`ProjectSettingsSection.tsx:179 / :178`。`deploy_command` / `dev_command` 内容错误仅在 deploy.run / 预览启动运行期暴露（现有 422 只拦「未配置」）。命令本质不可静态验真，可做的是弱校验（非空 trim/危险字符提示）或「试运行」按钮（需后端支持）——价值/成本比低，建议挂账。

### 5. [MINOR] ChannelSettingsModal 数值字段静默丢弃越界值

`ChannelSettingsModal.tsx:197`（单次提案节点上限）与 `:230-254`（提醒/护栏阈值 5 项，NumRow）。越界/非法输入被 `buildPatch`/`parseNum` **静默不提交且无 inline 提示**，用户误以为已保存。修法纯前端：inline error + 保存按钮 disabled 联动。

## 排查中证伪的候选（勿重复怀疑）

- **技能白名单**：`AgentDetailScreen` SkillsTab（:394）**已消费** `detected_runtimes[runtime].skills` 做候选池+池外标注+自由输入兜底——合规，且是 #1/#2 的体例参照。
- **cron 手填**：前端不存在 cron/cadence 输入框（reminders 创建在 Agent/MCP 侧），`lib/cron.ts` 校验器服务于展示。
- **模板向导角色映射 / 草稿层与 delta 改 owner**：均已是成员/Agent select（TemplateWizard :243 / DraftLayer :395），合规。
- CanvasTab Check 命令（:913）为临时命令自由文本，无权威源，合规。

## 修复批建议（owner 裁决）

| 批 | 项 | 性质 | 建议归属 |
| --- | --- | --- | --- |
| 前端小批 | #1 + #2（含布局顺序）+ #5 | 纯前端零契约零迁移，参考实现在库内 | 可并入 **B-M8-3**（同为 Members/创建面外壳件）或独立小批先行 |
| 需后端 | #3（路径存在性探测端点） | 新端点 + daemon 指令，契约 B/D 面 | 挂账，M8c 后另裁 |
| 挂账 | #4（命令弱校验/试运行） | 价值/成本比低 | 观察项 |

## 修复落地（2026-07-14，纯前端小批）

**已修 = #1 + #2 + #5**（owner「开始修复」拍板；#3 需后端、#4 观察项不在本批）。改动 6 文件，零契约零迁移零后端：
- `CreateAgentModal.tsx` + `create-agent.css`：机器选择器上移到 runtime/model 之上；runtime 分段消费 `detected_runtimes[].installed` 置灰未安装项（`（未安装）`标注 + 禁用），所选 runtime 在该机未安装则警示 + 阻断创建；model 输入接 `<datalist>` 候选池（= 所选机器该 runtime 探测 `models`），保留自由输入；无探测数据时不阻塞（回退旧行为）。消费与 AgentDetail ProfileTab 同源。
- `ChannelSettingsModal.tsx` + `channel-settings.css`：数值字段即时校验（`numError`），越界/非法 → 行内报错（`aria-invalid` + 文案）+ 禁用「保存」，杜绝越界值被 `buildPatch` 静默丢弃后误判已保存；回落合法值即恢复可保存。

**守门（全绿）**：web vitest **517 passed**（原 512：ChannelSettings 替 1 增 2、CreateAgent 增 4）· `pnpm typecheck` 0 错（web+contracts-ts tsc + pyright）· `pnpm -F web build` 绿 · `pnpm gen` 无生成物变更。

**真机 verify（mock-server + 真前端浏览器驱动）**：
- **#1 实机确认**：创建 Agent 弹窗 model 输入 `list="ca-model-list"`，datalist 候选 = 所选机器该 runtime 探测 models——claude_code → `[opus, sonnet]`，切 Codex 动态变 `[gpt-5-codex]`；placeholder 随池非空切「选择或输入模型标识」；机器选择器已在 runtime/model 之上（DOM 实证）。
- **#2**：seed 两 runtime 均 installed，实机观测到「enabled 随 installed 派生、候选池随 runtime 联动」同一代码路径；置灰/警示/阻断/恢复由 3 条新增单测覆盖（未安装 codex 置灰、默认 runtime 未安装警示+阻断、切装好的恢复）。
- **#5**：由 2 条单测驱动真实组件 + DOM 断言覆盖（越界 → `aria-invalid`+「不超过 50」+ 禁用保存；回落合法值 → 恢复可用并提交）。full-app 浏览器打开频道菜单受 mock 外壳 iframe 坐标/frame 错位阻碍（叠加本机截图工具超时，与 M8b 记录一致），故 #5/#2 的红态以组件级单测为准。
