# M8-DEV-PLAN —— 逐模块执行计划与进度表

| 项 | 内容 |
| --- | --- |
| 建立 | 2026-07-14，随 [M8-HANDOFF.md](M8-HANDOFF.md) 立项建立（任务书为范围与 DoD 权威，本文只管执行编排与进度） |
| 协作模式 | [COLLAB-MODEL.md](COLLAB-MODEL.md) v2「Fable 单窗编排」续用：波内并行派子代理执行/评审；**Fable 亲做关口** = L4a 读循环并发改造、L7 内核三处同步、L8 CAS 正确性、L13 实机 verify、/code-review 终裁 |
| 设计权威 | O8 全部实现语义 = [Orchestrator汇总设计.md](../../../orchestrator_docs/Orchestrator汇总设计.md) v1.0（§4 摘要 / §5 W9 / §6 护栏 / §8 质量回路）；实施与设计冲突时**先升设计文档版本再动代码** |
| 收口守门 | `uv run pytest -q`（基线 **1075**/4 只增不减）· `pnpm -F @coagentia/web test`（**403** 只增不减）· `pnpm typecheck`（pyright 0）· `uv run ruff check .` · `pnpm gen` 后 diff 空（golden **58**+新 partial 判例）· `pnpm -F @coagentia/web build` |

## 1. 波次编排

### 块 M8a 加固批

| 波 | 模块 | 并行性 | 备注 |
| --- | --- | --- | --- |
| a-0 | **L0 契约落笔 + contracts 同步** | 单件先行 | A v1.0.12 / B v1.5.1 誊写（照汇总设计 §9）+ manifest/mock/gen；半天件 |
| a-1 | **L1 原子建边** ∥ **L4 残留收敛** ∥ **L6 展示面残窗** ∥ **B-M8-1 ②③④**（深链/遮挡/R-13 纯前端件） | 四路并行（canvas 路由 / hub·held_drafts / deploy 日志 / web 文件域不相交） | **L4a Fable 亲做**：先写"真适配器时序仿真"红测（ack 前发 status 上报撞锁）再动读循环结构；B-M8-1 ①（上游多选）等 L1 端点就绪接线 |
| a-2 | 块收口：全量守门 + 交接文档同步 + 阶段小结 | — | 出口 = 任务书 §9a #1–#6 |

### 块 M8b O8 编排质量线（块 a 收口后开工）

| 波 | 模块 | 并行性 | 备注 |
| --- | --- | --- | --- |
| b-1 | **L7 迁移 0012 + W9 内核** | 单件先行，**Fable 亲核** | 纪律 8 首改 graph 组：golden 判例先扩 partial 用例、py/ts 双跑守门立起，再让 strict 全量回归证明零变化；landing 默认 partial 一行随此波 |
| b-2 | **L8 汇总执行域** ∥ **L9 质量回路+话术** | 双路并行（hub/canvas 汇总域 / proposal 落地钩子+role_templates 文件域不相交） | L8 的 summary_runs CAS 与唤醒抑制双面 Fable 亲审；L9 复用 inject_guard_feedback 零新帧 |
| b-3 | **B-M8-2 O8 可见面** | L8 形状就绪后接线 | 横幅/badge/改档/阻断-恢复 |
| b-4 | 块收口：O8 真机场景（任务书 §9b #11，可与 L13 合并跑）+ 全量守门 | — | 出口 = §9b #7–#12 |

### 块 M8c 外壳与收官（块 b 收口后开工）

| 波 | 模块 | 并行性 | 备注 |
| --- | --- | --- | --- |
| c-1 | **B-M8-3 + L10 外壳** ∥ **L11 打招呼** | 双路并行 | 均为小件；L11 owner 可否决（任务书裁决 #9），否决即划去 |
| c-2 | **L12 编排体验收官** | 单件 | C1 多节点真机演示（番茄钟系扩展）+ 教程章节 + C2 delta UX 复盘 + C3 全链路收官——踩在 O8 已收口真机上 |
| c-3 | **L13 m8_verify 实机收口** | 单件，**Fable 亲做** | 加固批探针 + O8 全链 + 外壳真机 + 浏览器 E2E；证据归档 M8-EVIDENCE.md |
| c-4 | /code-review high 终收口（多维 finder → 对抗核实 → Fable 终裁）→ 任务书归档 | — | M6/M7 收口惯例 |

## 2. 防返工锚点（开工前读一遍）

1. **L4a 三不变量**：帧内顺序（同连接上报按到达序生效）、ack 语义（daemon 重传判据不变）、emit 时序（gateway_tx 提交后事件才出）——写队列改造若破坏任何一条即回退重设计。
2. **L7 "新增档不动旧档"**：upstream_policy 默认 'strict' 必须让全部既有测试与 golden 逐字节不变；partial 只以新判例进入。derive_blocked 签名变更须 py/ts 同一提交内齐改。
3. **L8 摘要幂等**：指纹未变不重发——否则每次唤醒刷一条系统消息，摘要消息自身又是唤醒触发，成自激振荡。指纹比对在发送判定处单点。
4. **L8 轮计数与既有投递解耦**：计轮挂在"因该汇总任务向 owner 投递唤醒"这一个点；不要在多个唤醒入口各自 +1（重复计数）。
5. **L1 复用步进原子形**：节点+入边一个事务；不要"先建节点再补边"两段式（那正是空成功窗口本身）。
6. **R-14 前端拼接**：以 chunk_seq 为唯一去重键；不要按行文本去重（日志行可重复合法）。
7. **O8 红例用 daemon-sim**：可控多轮唤醒/可控空转；真 CLI 只进 L12/L13 演示位。
8. **CR-M8 教训随身**：探针环境自带 env（PYTHONIOENCODING 等）会掩盖被测缺陷——L4 时序仿真要在"不带救生圈"的裸配置下跑。

## 3. 进度表（随模块完成更新）

| 模块 | 状态 | 完成提交 | 备注 |
| --- | --- | --- | --- |
| L0 契约落笔+登记 | ✅ 完成 | `3532b6a` | A v1.0.12 / B v1.5.1 落笔 + contracts（UpstreamPolicy/SummaryRun/upstream_node_ids/RULE_CODES O8·O9）+ gen 确定。**执行决策：迁移 0012（summary_runs + canvas_nodes.upstream_policy）与 ORM 随 L0 落地**（原 DEV-PLAN 置于 L7）——因 test_schema_conformance 反射 canvas_nodes 列集须与 CanvasNodeRow 同步，否则块 M8a 全绿门守不住；列默认 strict 行为逐字节不变、summary_runs 无运行期消费，纯 schema 前移零风险。**L7（M8b）改为只做 W9 内核双档 + golden partial + landing 默认 partial + patch_node 改档**（迁移已落）。守门：contracts 125 / 全量 1082 / gen 确定 / pyright 0 / ruff 净 |
| L1 原子建边 | ✅ 完成 | `3f002fd` | POST /nodes 消费 upstream_node_ids 同 tx 建节点+入边；悬空 422 全量收集 + 回滚；K1 空成功窗口回归转绿（携未完成上游即 blocked）。canvas 22 / gating·system_nodes·conformance 88 绿 |
| L4 残留收敛（a/b/c） | ✅ 完成 | （待提交） | **L4a**：读循环收帧入队 + 独立 writer 消费（DB 写 offload 到 to_thread，不阻塞 loop、不撞锁撕连接）；_spawn 线程安全 + _system_pending threading.Lock；心跳写移出读循环；ack 语义/帧序/emit 时序保。**L4b**：discard 预检 agent_daemon_online + inject 挪 tx.after_commit。**L4c**：reevaluate 勘查确认已提交后（既有钉住测试 test_reevaluate_advances_read_position）。sync() 屏障语义调整（非 ack 上报改异步）+ drain_reports 屏障；daemon 55 / 全量 801 绿；+2 L4a 探针（写错不撕连接 / 阻塞写不阻塞读循环） |
| L6 R-10/R-14 | 未开工 | — | |
| B-M8-1 加固前端四件 | 未开工 | — | ②③④ 可即刻开工 |
| —— 块 M8a 收口 —— | — | — | |
| L7 W9 内核（0012 已随 L0 落） | 未开工 | — | Fable 亲核；**迁移 0012 已随 L0 落地**，L7 只剩 W9 内核双档 satisfied + golden partial + landing 默认 partial + patch_node 改档 |
| L8 汇总执行域 | 未开工 | — | |
| L9 质量回路+话术 | 未开工 | — | |
| B-M8-2 O8 可见面 | 未开工 | — | |
| —— 块 M8b 收口 —— | — | — | |
| B-M8-3+L10 外壳 | 未开工 | — | |
| L11 打招呼（默认关） | 未开工 | — | owner 可否决 |
| L12 体验收官+教程 | 未开工 | — | |
| L13 m8_verify 收口 | 未开工 | — | Fable 亲做 |
| /code-review 终收口 | 未开工 | — | |
