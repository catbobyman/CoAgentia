# COLLAB-MODEL —— M6 三模型协作模式（Codex / Opus 4.8 / Fable 5）

> 建立：2026-07-11（M6a 波 2 施工中）。适用范围：M6 剩余全程;M7 立项时复审沿用。
> 分工原则 = **能力-风险匹配**：规格完备 + 有确定性守门（golden 双跑/conformance/幂等测试）的模块 → 执行模型;并发×崩溃不变量、跨系统实机诊断、对抗性审查、契约级技术把关 → Fable 5。依据 = M5b 实证（三个最严重 bug 全部出在落地/幂等域;单测全绿的缺陷在实机现形）。

## 1. 角色分工

| 角色 | 定位 | 承担 | 不做 |
| --- | --- | --- | --- |
| **Codex**（现役） | M6a 执行者 | 波 2 提交 → 波 3（J4 ∥ J5 ∥ J6 ∥ B-M6-1）→ 守门全绿 → 文档实况化 → **停在「M6a 实机 verify」行前汇报** | 实机 verify / code-review / 契约擅改 |
| **Opus 4.8**（接棒） | M6b 执行主力 | J7 同构内核 ∥ J11 骨架拼装 → J8 提案域 → **J9 初稿** → J10 delta ∥ B-M6-2 前端;同一套 AGENTS.md/任务书/DEV-PLAN 纪律 | J9 未过 Fable 审查不得进波 4;J11 原创话术定稿 / J12 / code-review |
| **Fable 5**（收口与高危段） | 质量关口 | **M6a 收口段**（并行审计 → 实机 verify → /code-review high → 收口提交）;**J9 对抗审查 + fail-closed·A5 亲验**;**J11 成员级话术定稿与实机校准**;**M6b 并行审计 → J12 实机 verify → /code-review high → M6 里程碑收口**;全程契约级裁决的技术把关 | 规格完备模块的日常施工 |
| **Owner** | 路由与拍板 | 会话切换;裁决转发（执行者 → Fable 把关 → 批复回执行者）;大方向拍板 | 已拍板项不重复裁决 |

## 2. 阶段流水与交接点

```
T0  Codex：波 2 单提交 → 波 3 施工 → 守门 3 全绿 → 文档实况化 → 停 · 汇报
T1  Fable：M6a 收口 —— 并行审计(workflow) → 实机 verify(M6A-EVIDENCE+截图)
          → /code-review high → 修复 → 收口提交 + CURRENT-HANDOFF/RECORD 同步
T2  Opus：M6b 波 1（J7 内核 ∥ J11 骨架[§13.1/§12 原文注入+upsert,话术留 TODO 标记]）
          → 守门 → 波 2（J8 提案域）→ 波 3 前半（J9 初稿,含 fail-closed 复核初验+A5 测试）
T3  Fable：J9 对抗审查关口 —— 逐不变量审查(CAS 竞态/重放保序/req_hash 身份/
          fail-closed 回滚持久性) + 亲跑 A5 崩溃重放;**≥1 个 blocking 级缺陷则由
          Fable 重写该段**;通过才放行波 4
T4  Opus：波 4（J10 delta+O9 ∥ B-M6-2 后半）→ 守门全绿 → 文档实况化 → 停 · 汇报
T5  Fable：M6b 收口 —— J11 话术定稿(真 LLM 试拆解校准一次通过率) ∥ 并行审计
          → J12 实机 verify（三场景+A1–A8,M6-EVIDENCE）→ /code-review high
          → M6 里程碑收口(任务书移 archive/)
随时  契约级缺口：执行者隔离该路径继续其余工作 → owner 转 Fable 技术把关 →
     批复回执行者（已运转三轮的既有模式,异步不阻塞）
```

## 3. 交接协议（五条,对三个模型同等适用）

1. **文档即交接**：移交前必须把 [M6-DEV-PLAN.md](M6-DEV-PLAN.md) 进度表（状态+提交哈希+守门数字）与 [CURRENT-HANDOFF.md](CURRENT-HANDOFF.md) 更新到实况——接棒方**只信文档不信转述**;Claude 系会话虽继承项目记忆,文档仍是唯一权威。
2. **git 态移交**：移交时工作树干净（波次已提交）;若有未提交改动必须在 CURRENT-HANDOFF 写明原因与范围（Codex 波 2 持锁待裁决是合规先例）。
3. **裁决路由**：契约级缺口 → 停该路径不停全局 → owner 转 Fable → 批复含精确化条款 → 执行者按批复落笔（升版本+变更记录+header 同步）。执行者不擅改契约,Fable 不越过 owner 直接下达。
4. **守门不随模型降级**：守门命令、基线只增不减、conformance 双跑、golden 双跑、进度表义务对任何执行者同等生效;交接点本身不豁免守门。
5. **证据义务**：实机结论必须截图/探针数字实证归 `docs/verify/`;审查结论必须逐条可复核（文件:行 + 失败场景），禁止"看起来没问题"。

## 4. 入场提示词模板

### 4a. 给 Opus 4.8 的 M6b kickoff（T2,owner 复制即用）

```text
你接手 CoAgentia 项目 M6b「Orchestrator 拆解链」的执行主力工作(M6a 已由 Codex
完成并经 Fable 5 收口)。按序读:仓库根 AGENTS.md → docs/project-handoffs/
CODEX-CONTEXT.md(接手者通用上下文,不限 Codex) → CURRENT-HANDOFF.md →
M6-HANDOFF.md(任务书) → M6-DEV-PLAN.md(§0 块 b 波次表) → COLLAB-MODEL.md
(你的角色边界) → orchestrator_docs/Orchestrator任务拆解设计.md(M6b 实现级权威,
全文精读)。
你的范围:块 b 波 1(J7 ∥ J11 骨架拼装——§13.1/§12 原文注入,成员级原创话术留
TODO 交 Fable 定稿) → 波 2(J8) → J9 初稿(含 fail-closed 复核与 A5 崩溃重放
测试初验) → 【停,J9 交 Fable 对抗审查,通过后】 → 波 4(J10 ∥ B-M6-2)。
J12 实机 verify 与 code-review 不归你。守门/文档/裁决路由规矩同 AGENTS.md;
J9 三条历史教训(DEV-PLAN §3:reserve-before 校验前置/req_hash 折 source 身份/
重放按 seq 保序)是审查关口的既定检查项,写的时候就对着自查。
```

### 4b. 召唤 Fable 5 的收口/审查会话（T1/T3/T5,owner 复制即用）

```text
[T1] M6a 已由 Codex 完成到实机 verify 前,读 CURRENT-HANDOFF/M6-DEV-PLAN 接手:
     并行审计 → 实机 verify(M6A-EVIDENCE+截图) → /code-review high → 收口提交。
[T3] Opus 已交 J9 初稿(见 DEV-PLAN 进度表),做对抗审查:逐不变量(CAS/保序/
     req_hash 身份/fail-closed 回滚持久性)+ 亲跑 A5;有 blocking 缺陷你重写。
[T5] M6b 波 4 已完,做收口:J11 话术定稿与实机校准 ∥ 并行审计 → J12(三场景+
     A1-A8,M6-EVIDENCE) → /code-review high → M6 收口,任务书移 archive/。
```

## 5. 为什么这样切（一句话版）

- **J9 设审查关口**：M5b 三个最严重 bug（幂等击穿/保序漂移/身份漏折）全在同域,规格完备也没拦住——不变量推理必须上最强模型二道把关。
- **J12/verify 归 Fable**：真 daemon+真 LLM+浏览器三层同跑的失败归因、Orchestrator 真模型行为校准、"哪里值得怀疑"的判断,是 verify-surfaced bug（M5b classifyRole/布局）证明过的强模型价值区。
- **J7/J8/J10/前端归执行模型**：V1–V14 连错误码都定死、golden 逐字节守门、状态机逐条条文——照契约填空 + 自审,Codex 在 M6a 已证明此类工作可靠。
- **J11 拆两半**：骨架拼装是搬运（Opus）;原创话术+校准与 J12 同回路（Fable）。
