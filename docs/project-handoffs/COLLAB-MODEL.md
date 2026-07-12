# COLLAB-MODEL —— M6 多模型协作模式（v2：Fable 单窗编排）

> 建立：2026-07-11（v1「owner 切窗口」多模型模式）。**v2 修订：2026-07-12，owner 拍板改制**——剩余 M6 工作收敛为**一个 Fable 5 主会话在会话内编排**：执行与评审派 Opus 4.8 子代理（`model: opus`），关键任务 Fable 本体亲做，owner 只保留契约级拍板。v1 的 T0–T5 切窗口机制废止（T0/T1 已按 v1 完成，作为历史保留在 §6）。
> 分工原则不变 = **能力-风险匹配**：规格完备 + 有确定性守门的模块 → Opus 子代理;并发×崩溃不变量、跨系统实机诊断、终裁、契约级技术把关 → Fable 本体。依据 = M5b/M6a 实证（最严重 bug 集中在落地/幂等域;单测全绿的缺陷在实机现形）。

## 0. 当前基点（v2 生效时）

M6a 已全收口：实现（波 1–3，Codex 主驱，`d564ebf`→`62939f2`→`6f6fc93`）+ 实机 verify 20/20（`bc70cd5`，M6A-EVIDENCE）+ /code-review high 10 findings 全修（`404aaa8`，T1 Fable 收口）。**接续 = M6b 全程,适用本 v2 模式**;Codex 谢幕（如需返场由 owner 另行指令）。

## 1. 角色分工（v2）

| 角色 | 形态 | 承担 | 不做 |
| --- | --- | --- | --- |
| **Fable 5** | 主会话主循环（总编排者） | 编排与集成把关（子代理产出过目后才提交）;阶段间守门;**J9 逐不变量对抗审查 + A5 亲验（硬关口,blocking 级缺陷亲自重写）**;**J11 成员级话术定稿与真 LLM 校准**;**J12 实机 verify（PRD M6 出口,亲自）**;code-review 的**汇总去重与存疑终裁**(必要时运行时实证裁决——M5b「ApiError 回滚」误述教训);涉及幂等/事务/gating 的**正确性关键修复亲自改**;契约级缺口的技术把关与向 owner 报选项 | 规格完备模块的日常施工;常规评审下场 |
| **Opus 4.8** | 子代理（`model: opus`,按需并行） | **全部执行模块**：J7 同构内核 / J11 骨架拼装（话术留 TODO）/ J8 提案域 / **J9 初稿** / J10 delta+O9 / B-M6-2 前端 / 模板 PATCH·DELETE;**并行审计 finder**;**code-review 全程——8 维度 finder 与逐条对抗核实 verifier 均为 Opus**(v2 修订:评审面整体由 Opus 承担);常规修复 | 终裁;J9 审查;两次实机 verify;话术定稿;擅改契约 |
| **Codex** | 已谢幕 | （M6a 实现已交付） | — |
| **Owner** | 拍板 | 契约级裁决、方向分歧、出口达成验收;发起会话与预算控制 | 已拍板项不重复裁决;不再做会话间路由 |

## 2. 阶段流水（v2,单会话内推进;阶段间守门全绿才前进）

```
✅ 阶段 0|M6a 收口 —— 已按 v1 完成(verify `bc70cd5` + review `404aaa8`)
■ 阶段 1|M6b 波 1-2 —— 执行:Opus 子代理;集成守门:Fable
   J7 内核 ∥ J11 骨架(并行,文件域不相交) → 守门/commit → J8 提案域 → 守门/commit
■ 阶段 2|J9 —— 初稿:Opus 子代理;审查:Fable 本体(硬关口)
   Opus 写初稿(含 fail-closed 复核+A5 测试) → Fable 逐不变量对抗审查+亲跑 A5
   → blocking 缺陷 Fable 亲自重写 → 通过才进阶段 3
■ 阶段 3|M6b 波 4 —— 执行:Opus 子代理(并行)
   J10 delta+O9 拦截 ∥ B-M6-2 前端 → 守门/commit
■ 阶段 4|M6 收口 —— Fable 主导,评审面 Opus
   J11 话术定稿(Fable 亲自) ∥ 并行审计(Opus finder,Fable 逐条核实) →
   J12 实机 verify(Fable 亲自,三场景+A1-A8,M6-EVIDENCE) →
   /code-review high(**finder+verifier 全 Opus,Fable 只终裁**;常规修复派 Opus,
   正确性关键修复 Fable 亲自) → 守门 → M6 里程碑收口(任务书移 archive/)
随时|契约级缺口:停该路径不停全局 → Fable 技术把关后向 owner 报选项(方案+替代+
   推荐) → 拍板后按批复落笔(升版本+变更记录+header 同步)
```

## 3. 协作协议（v2 六条;对主循环与全部子代理同等生效）

1. **文档即事实**：每阶段完成即更新 [M6-DEV-PLAN.md](M6-DEV-PLAN.md) 进度表（状态+提交哈希+守门数字）与 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)——会话中断/续开时,新会话只信文档不信记忆。
2. **子代理简报自足**：指明读 [CODEX-CONTEXT.md](CODEX-CONTEXT.md)+任务书对应 J 行+契约对应节+DEV-PLAN §3 锚点;按 DEV-PLAN 文件域表分派,**不相交才并行**;子代理产出主循环过目集成后才提交。
3. **模型分派纪律**：执行/审计/评审子代理一律 `model: opus`;Fable 本体只花在编排、终裁、J9 关口、两次实机 verify、话术定稿与正确性关键修复——**评审面 Opus 化的兜底 = 存疑结论的终裁权保留在 Fable**。
4. **守门不降级**：守门命令、基线只增不减（M6a 收口态起算,以 CURRENT-HANDOFF 最新数字为准）、conformance/golden 双跑、进度表义务不因模型或阶段豁免。
5. **裁决路由**：契约级缺口不擅改;Fable 把关后向 owner 报选项等拍板;已拍板四项（交付链先行/worktree=消息注入/挂账三件全收/合并 --no-ff）与任务书 §7 十六裁决勿再问。
6. **证据义务**：实机结论截图/探针数字归 `docs/verify/`;审查结论逐条可复核（文件:行+失败场景）;实机起的进程结束必杀。

## 4. 入场提示词（owner 开新 Fable 5 窗口复制即用;v2 唯一入口）

```text
你是 CoAgentia 项目 M6b「Orchestrator 拆解链」的总编排者与质量关口(Fable 5)。
授权你使用多 agent 编排(workflow/子代理)。工作目录 D:\Project4work\Agenthub_7_8\coagentia。

【背景】M6a 已全收口(实现 Codex 波 1-3 + 实机 verify 20/20 `bc70cd5` + code-review
10 findings 全修 `404aaa8`)。M6b 未开工。你按 COLLAB-MODEL v2 推进到 M6 里程碑收口:
执行与评审派 Opus 子代理(model: opus),关键任务亲做,code-review 你只终裁。

【建立上下文(按序读)】docs/project-handoffs/CURRENT-HANDOFF.md → M6-HANDOFF.md
(任务书 §4 J7-J12/§7 裁决/§9b 出口) → M6-DEV-PLAN.md(块 b 波次表+§3 锚点,进度表
由你维护) → COLLAB-MODEL.md(v2 分工) → CODEX-CONTEXT.md(子代理简报引用源) →
orchestrator_docs/Orchestrator任务拆解设计.md(实现级权威,精读) → engineering_docs
六契约按模块取用(B §12 是 M6 行为条文)。

【阶段与模型分工】
■ 阶段 1|波 1-2(执行=Opus 子代理,集成守门=你):
  并行派两个子代理(model: opus,文件域不相交)——
  · J7 同构校验内核:contracts kernel 新增 decomposition(<control> 解析/V1-V14
    全量收集/errors v1/指纹复用 kernel/fingerprint 勿重写)+web lib/decomposition.ts
    镜像+fixtures/golden/decomposition.json(每条规则至少一红一绿),py+vitest 双跑
    逐字节一致。
  · J11 骨架:Orchestrator builtin 角色模板(拆解设计 §13.1 七条+§12 规模判断表
    原文注入,upsert 照 templates/builtin.py;成员级原创话术留 TODO 归你阶段 4)
    +模板 PATCH/DELETE(builtin→409 TEMPLATE_BUILTIN_IMMUTABLE)。
  → 守门→commit → 派子代理(model: opus)做 J8:0009 迁移(proposals,单一非终态
  部分唯一索引)+orchestration/proposal.py(8 态状态机/三入口归一/上下文注入/消息
  落库 <control> 挂接/修复循环 S1 直投照 hub inject_guard_feedback 先例/Superseded
  与 rev 配额/对账 #6/AwaitingConfirm 24h 提醒) → 守门→commit。
■ 阶段 2|J9(初稿=Opus 子代理,审查=你,硬关口):
  子代理写初稿:confirm CAS(S2 指纹/409 携最新态)/调整服务端全量重验/adjustments·
  landed_hash 落账/落地事务(decomp: 前缀,照 templates/service.py instantiate_template
  体例写第二个落地批消费者:拓扑序 create_node/汇总节点条件追加/merge 系统节点自动
  追加=裁决 #6/直落 auto(channel-policy)/:done 后发已落地消息)/对账 #4/fail-closed
  持久性复核(M5 挂账必做)+A5 测试。
  你亲自逐不变量对抗审查:可失败校验全部前置于 reserve-before/req_hash 折入 source
  身份(proposal id+landed_hash)/批内重放按 ledger seq 保序/CAS 竞态/fail-closed
  回滚持久性;亲跑 A5(落地中 kill→重启补齐,无重复无缺失,已落地消息恰一条)。
  blocking 级缺陷你亲自重写。通过才进阶段 3。
■ 阶段 3|波 4(执行=Opus 子代理,并行):
  · J10:delta(base 指纹/结果图重验/NODE_ACTIVE/部分接受 removed_ops/delta: 幂等)
    +O9 拦截(canvas 结构写端点对 Agent 主体 403 rule=O9,人类不受限)。
  · B-M6-2:拆解入口+无 Orchestrator 引导/提案卡/草稿层 TS 内核实时校验防呆确认/
    delta 绿红高亮+逐 op 剔除面板/rev 替换/P12 编排组/wsBridge 接 draft.*、delta.*、
    landing.*、proposal.updated。
  → 守门→commit。
■ 阶段 4|M6 收口(你主导,评审面=Opus):
  J11 话术定稿(你亲自:成员级 description 补写,笔法照 templates/builtin.py,真 LLM
  试拆解校准) ∥ 并行审计(4-6 个 model: opus finder 按维度审 M6b 全量 diff,你逐条
  核实先修) → J12 实机 verify(你亲自)=PRD M6 出口:一句话需求 @Orchestrator→提案卡
  +草稿画布→人工调整→确认落地→两 writes_code 任务并行 worktree 交付→合并成功;
  制造冲突→派回解决;测试桩致校验失败→修复循环自动改好;连续 2 次失败→Failed @人类;
  顺带 single_task/直落/delta 部分接受/A5;拆解设计 A1-A8 逐条勾销,证据+截图归
  docs/verify/M6-EVIDENCE.md → /code-review high:8 维度 finder 与对抗核实 verifier
  全部 model: opus,你不下场评审,只汇总去重、存疑终裁(必要时运行时实证)、组织修复
  (常规修复派 opus,幂等/事务/gating 正确性修复你亲自) → 守门 → M6 里程碑收口:
  任务书移 archive/,CURRENT-HANDOFF/PROJECT-RECORD/M6-DEV-PLAN 终态同步。

【规矩】COLLAB-MODEL v2 §3 六条协作协议对你与全部子代理同等生效;实现=契约填空
未列出的不发明;契约级缺口停路径报 owner 选项;已拍板勿再问;挂账未点名不顺手修;
win32(taskkill /F /T、stdout utf-8、git 边界按 DEV-PLAN §3 校准结论);全程中文,
结论截图实证。唯一停下等 owner:契约级裁决/方向分歧/出口达成汇报。
```

## 5. 为什么这样切（v2 增补）

- **J9 关口不变**：M5b 三个最严重 bug（幂等击穿/保序漂移/身份漏折）全在同域——不变量推理必须最强模型二道把关,且重写权在关口手里。
- **J12/verify 归 Fable 不变**：跨系统失败归因与真 LLM 行为校准是 verify-surfaced bug 证明过的强模型价值区;M6a verify 亦由 Fable 亲跑（20/20）。
- **code-review 评审面 Opus 化（v2 修订,owner 拍板）**：finder+verifier 双层全 Opus,成本显著下降;质量兜底 = **存疑结论终裁权保留 Fable**——M5b 那次评审 agent 对事务回滚机制的误判,正是在终裁层被运行时实证纠正的,该层不降级。
- **单窗编排替代切窗口（v2 修订）**：M6a 施工期结束后,剩余工作以关口活为主、执行活可全部子代理化——主循环模型应匹配主循环工作性质;owner 从"会话路由器"退回"拍板者"。

## 6. 历史：v1 模式存档（已完成部分）

v1 = owner 切窗口的三模型接力（Codex→Opus→Fable,T0–T5 交接点+入场提示词）。实际执行:T0（Codex 完成 M6a 波 1–3,超预期含全部波 3）与 T1（Fable 收口:审计→verify `bc70cd5`→review `404aaa8`）按 v1 完成;T2–T5 被 v2 单窗模式取代,原 §4a/§4b 提示词废止。完整 v1 文本见 git 历史 `1633ad9`。
