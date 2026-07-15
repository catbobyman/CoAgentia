# B-5 Agent 无契约提交通道 — 修复方案（供 owner 拍板）

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-15；问题实证 = [M8-REALTEST-FINDINGS.md](verify/M8-REALTEST-FINDINGS.md) B-5（4/4 Agent 命中） |
| 问题 | T7 门要 TaskHandoff 才能置 in_review/done，但 16 个 MCP 工具**无契约提交工具**；系统注入的起草指令让 Agent「通过 POST /tasks/{id}/contracts 提交」——Agent 够不着 REST；set_task_status 422「缺交接材料」无格式提示 → 交付收尾链在**能力面**断裂 |
| 决策性质 | **工具扩面 = owner 既往亲自拍板项**（M7 `trigger_deploy` 先例，「零工具连胜」止于 M6 由 owner 破例）；本文档给设计 + 选项，落笔前请你拍板 |
| 推荐 | **新增单个 MCP 工具 `submit_task_contract(task_id, kind, body)`**（代理 POST /tasks/{id}/contracts，覆盖 TaskPlan+TaskHandoff 两 kind）+ 话术三处 + HANDOFF_INCOMPLETE 422 带格式 hint。契约 E 16→17；零迁移、零新端点、零新 WS 帧 |
| **裁决（owner 拍板 2026-07-15）** | **D1/D2 = 单工具 + body free-form（推荐案）**；**D3 = 话术+hint 同批**。设计锁定，可按 §2 实施 |
| **实施（2026-07-15）** | **✅ 已按 §2 落地**（守门 pytest 1186/4·vitest 538·pyright0·ruff净·gen确定）。实施记录见 §7。待做 = 零人工全链复跑真机验证（复跑前重启 server+daemon） |

## 1. 事实底座（全部本轮代码核实）

| # | 事实 | 出处 |
| --- | --- | --- |
| F1 | MCP 工具目录 = **16 个纯代理工具**（每工具↔一 REST 端点，daemon `adapters/mcp.py` 零业务规则）；有 `set_task_status`（POST /status）但**无契约提交** | [constants.py:215](../packages/contracts/src/coagentia_contracts/constants.py) `COAGENTIA_MCP_TOOLS` |
| F2 | 加一个工具 = 三处登记：`COAGENTIA_MCP_TOOLS`（契约 E 目录）+ daemon `TOOLS`（inputSchema）+ `build_request`（→REST 映射）。`trigger_deploy`（M7）是现成范式（空体代理 POST /deployments） | [mcp.py:236,314](../apps/daemon/src/coagentia_daemon/adapters/mcp.py) |
| F3 | 端点 **POST /tasks/{id}/contracts** 收 `ContractCreate={kind, body}`：`kind∈{TaskPlan,TaskHandoff}`，`body` 故意留 free-form JsonValue，**server 按 kind 二次 model_validate**；不符 → 422 `VALIDATION_FAILED` **携逐字段 loc/msg/type**；auth = acting_member（Agent 的 X-Acting-Member + Bearer computer key，daemon 代理已带） | [tasks.py:736](../apps/server/src/coagentia_server/routes/tasks.py)、[rest.py:475](../packages/contracts/src/coagentia_contracts/rest.py) |
| F4 | 死路真身 = 注入话术**字面让 Agent 打 REST**：`"请…通过 POST /tasks/{task_id}/contracts 提交"` | [hub.py:2634](../apps/server/src/coagentia_server/computers/hub.py) `inject_contract_draft_request` |
| F5 | T7 门在 set_task_status→in_review 时查 `active_handoff_missing` → 422 `HANDOFF_INCOMPLETE` rule=T7，details.missing=缺失字段清单，但**无「怎么补/用什么工具」的 hint** | [tasks.py:701](../apps/server/src/coagentia_server/routes/tasks.py) |
| F6 | TaskHandoffBody = version/from_member/to_member/deliverables[{path,kind}]/evidence[{type,ref,conclusion}]/open_risks[]/verify_plan/review_verdict?；TaskPlanBody = version/goal/acceptance_criteria[≥1]/defaults_decided[]/out_of_scope[] | [rest.py:427](../packages/contracts/src/coagentia_contracts/rest.py)、[entities.py:428](../packages/contracts/src/coagentia_contracts/entities.py) |

**关键洞察**：端点、校验、结构化 422 **全都现成且完备**——唯一缺口是 Agent **够不着这个端点**（无工具）。补上工具，「起草→提交→（若错）结构化 422→J8 式修复循环自愈」全链即通，不需要动 server 校验逻辑。

## 2. 设计（推荐案）

**新增 1 个工具 `submit_task_contract`**：

```
inputSchema = {
  task_id: string (required),
  kind: enum["TaskPlan","TaskHandoff"] (required),
  body: object (required)   # free-form，镜像 ContractCreate.body 的 JsonValue
}
build_request → POST /api/tasks/{task_id}/contracts  json_body={kind, body}
```

- **body 留 free-form + 富 description**（不在 schema 里硬编 TaskHandoffBody 全结构）：与 `ContractCreate.body=JsonValue` 的「server owns 校验」哲学一致（纪律 7 单一事实源，不在工具层复制第二份 schema）。description 里**逐字列明**两 kind 的字段 + T7 规则 + 一个最小示例，让 Agent 首投即知形状；万一漏字段，422 `VALIDATION_FAILED` 携 loc/msg 逐字段透传，J8 修复循环自愈（同 decompose 修复循环范式）。
- **覆盖 TaskPlan + TaskHandoff 两 kind**（同一端点白送）：既解 T7 交付收尾（Handoff），也解升格补计划（Plan）。LoopContract 属 Reminder 域不在此。

**配套三处话术 + 一处 hint（同批）**：
1. **F4 注入话术改指工具**：`inject_contract_draft_request` body 从「POST /tasks/{id}/contracts」改为「用 `submit_task_contract` 工具提交（kind=…）」+ 简述必填字段。
2. **role_templates 补交付收尾话术**：实现/评审角色模板加一节「置 in_review/done 前先 `submit_task_contract` 提交 TaskHandoff（deliverables≥1+evidence+verify_plan）」。
3. **HANDOFF_INCOMPLETE 422 带 hint**（F5）：details 加 `hint: "用 submit_task_contract 提交 kind=TaskHandoff，补齐 {missing}"`——对齐 J8「422 携格式 hint 自愈」哲学。

## 3. 影响面

| 面 | 结论 |
| --- | --- |
| 契约 E | **16→17 工具**（小版升，如 v1.5→v1.6）。目录 `COAGENTIA_MCP_TOOLS` +1 行 |
| 契约 A/B/C/D | **零变更**（端点/schema/事件/帧全现成；ContractCreate/TaskHandoffBody 早已定义） |
| 迁移 | **零**（无 schema 变更） |
| daemon | `adapters/mcp.py`：`TOOLS` +1 项、`build_request` +1 分支（≈范式复制 trigger_deploy） |
| server | 注入话术 1 处 + HANDOFF_INCOMPLETE hint 1 处（均纯文案/details，无逻辑变更） |
| role_templates | +1 节交付收尾话术 |
| 前端 | 零改动 |
| 测试 | daemon `test_adapter_mcp`（工具目录/build_request 断言）+新增 submit_task_contract 单测（TaskPlan/TaskHandoff 两 kind → 正确 REST；422 透传）；server 端点已有覆盖 |

## 4. 待你拍板的三个决策

| # | 决策 | 选项 | 推荐 |
| --- | --- | --- | --- |
| D1 | **工具粒度** | (a) 单工具 `submit_task_contract(kind, body)`；(b) 两工具 `submit_task_plan` / `submit_task_handoff`（各自扁平强 schema，16→18） | **(a)**：镜像 ContractCreate，纪律 7 不复制 schema，工具数少一个；缺点=body free-form 首投可能漏字段（靠 422+描述兜底） |
| D2 | **body schema 严格度** | (a) free-form object + 富 description；(b) 按 kind 硬编 oneOf 全结构 schema | **(a)**：与「server owns 校验」一致；(b) 让 Agent 首投更准但工具层背第二份 schema（漂移风险，违纪律 7） |
| D3 | **配套话术 + 422 hint 是否同批** | (a) 同批（注入话术改指工具 + role_templates + HANDOFF_INCOMPLETE hint）；(b) 只加工具，话术另批 | **(a)**：不补话术则 Agent 仍可能不知道用新工具（4/4 命中的是「不知道有通道」+「够不着」双缺口）；hint 低成本高回报 |

## 5. 测试计划（守门）

- daemon 单测：`submit_task_contract`（TaskPlan/TaskHandoff）→ POST /tasks/{id}/contracts、body 透传、kind 透传；未知/缺参防御；422 结构化透传。
- 工具目录 parity：`test_adapter_mcp` 断言 17 工具、`COAGENTIA_MCP_TOOLS` 与 daemon `TOOLS` 一致。
- server：`submit_task_contract` 端点 + T7 门 + HANDOFF_INCOMPLETE hint（既有 + 新增 hint 断言）。
- 全量 pytest/vitest/pyright/ruff/gen 只增不减；契约 E 目录变更须 `pnpm gen` 同步。
- （落地后）B-1+B-5 修齐 → **零人工辅助全链真机复跑**：拆解→claim→worktree→实码→`submit_task_contract` 交 handoff→T7 门放行→done→gating 解锁+解锁唤醒→merge→O8，全程 Agent 自主无 REST 代劳。

## 6. 开放问题（登记）

- **from_member/to_member 由谁填**：TaskHandoffBody 必填双方 member_id。Agent 填 from_member=自身、to_member=接收方（评审人/人类/下游）。若 Agent 不知自身 member_id → role_templates 话术须点明「你的 member_id 见 turn 上下文」；或 server 在 submit 时以 acting_member 回填 from_member（更稳，但破「纯代理」——登记，倾向话术解决先）。
- **是否顺带给 `request_contract_draft` 的 202 响应带工具名**（人类「让 Agent 起草」路径）：低优先，注入话术改了即可。
- LoopContract 提交通道（M4 循环任务上岗契约）当前也无工具——不在 B-5 范围（realtest 未触及），登记。

## 7. 实施记录（2026-07-15）

按 §2 推荐案落地，改动面与 §3 预测一致：

| 面 | 落地 |
| --- | --- |
| 契约 E 目录 | `COAGENTIA_MCP_TOOLS` +`submit_task_contract`（16→17，v1.6 注释；无机读版本常量，仅注释追踪） |
| daemon | `adapters/mcp.py`：`TOOLS` +1 项（富 description 逐字列两 kind 字段+T7 规则）、`build_request` +1 分支（POST /tasks/{id}/contracts，`{kind, body}` free-form 透传）、`_CONTRACT_KIND_VALUES` 从枚举派生（loop_contract 排除，端点会 422 拒） |
| server 话术 | ① `inject_contract_draft_request` 改指工具（不再指 REST）；② `HANDOFF_INCOMPLETE` 422 details +hint（`set_task_status` T7 门 + 升格门两处）；③ **交付收尾话术落基线身份文案** `cmdline.py::_IDENTITY_TEMPLATE`（无 impl/review 内置模板，基线是所有 Agent 共享的唯一proactive 面）——**修正 §2 点 2「role_templates」为「基线身份文案」** |
| 迁移/端点/WS/前端 | **全零**（工具目录 Python-only 不入 gen，前端零变更实证 = gen 零 diff） |
| 测试 | daemon `test_adapter_mcp` +6（build_request 两 kind/ContractCreate+kind 模型双校验/缺 body 防御/422 透传/201 成功/tools-list 断言）；`test_catalogs` +B5 专项 + `_tools_through_m6` 排除表扩 `submit_task_contract` + 总数守门 17；`test_contracts` 三处 T7 断言放宽为 missing+hint 分断言；身份文案关键词守门 +`submit_task_contract` |

**守门**：pytest **1186/4**（基线 1179→+7）·vitest **538**·pyright **0**·ruff 净·gen 确定。

**对抗复审无阻断项**：① auth 头 X-Acting-Member = agent member_id（from_member 由 Agent 自填，created_by=acting）；② kind 二次校验，loop_contract/非法 kind 均端点 422 结构化拒；③ body extra="forbid" → 漏/多字段逐字段 422 loc/msg；④ done 必经 in_review（TRANSITIONS 无 in_progress→done）故「in_review/done 前提交」话术准确；⑤ codex 同一 TOOLS 目录零改动生效。**§6 开放问题「from_member 由谁填」维持登记**（话术解决，realtest 复跑验）。
