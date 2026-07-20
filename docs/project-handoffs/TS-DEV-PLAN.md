# TS-DEV-PLAN：TS 迁移完整执行计划（批 A / B / C）

v1.0 · 2026-07-19 · owner 指示「写一份完整详细的迁移计划，保证高效也保证质量」。
定位：[TS-MIGRATION-ROADMAP.md](TS-MIGRATION-ROADMAP.md) 的执行细化，**开工前以本文为唯一计划事实源**；开工门 = §9 拍板点。本文所有规模/行为数字来自 2026-07-19 两路实机摸底（server 结构全图 + 数据层/node 运行时实测探针），非估计值另有标注。

---

## §0 总览

| 批 | 内容 | 规模（实测） | 前置 | 预估当量 |
| --- | --- | --- | --- | --- |
| **A** | py daemon 退役 | 删 48 文件；pytest 981→~751 收集显式重置 | owner 拍板 + A2 双轨观察 | 半天 + 观察期 |
| **B** | server TS 化 | src 60 文件 **13,396 行** / 80 端点 / 2 WS 面 / 35 表 / 测试 41 文件 **15,402 行 668 例** | 六裁决拍板（§5.1）+ B-cal 校准全 PASS | 3–4 当量日（daemon 批 7.5k 行/日实测外推，hub 单列一波） |
| **C** | mock-server 处置 | 1,265 行 | 无 | 零工作量（裁定保留，见 §6） |

**终态**：TS 运行时（web + daemon-ts + server-ts）+ **py 契约权威工具链**（contracts 19 + scripts 3 + mock-server 3 + alembic 迁移目录，~25 个 .py）。不追求 py 归零——契约、迁移、gen 链、conformance 夹具留 py 是裁决而非欠账。

---

## §1 目标与不变量（六条，违反即返工）

1. **契约零修订**：契约 A/B/C/D/E/E2 端点形状、WS 帧形、错误体 `{"error":{code,message,rule,details}}`（ErrorCode 32 员）逐字节不变。实现换语言，wire 面不许动。
2. **SQLite 库文件同构**：server-ts 读写**同一个** `~/.coagentia/server/coagentia.db`，同一 schema、同一 `alembic_version` 机制、同一 PRAGMA 组（foreign_keys=ON / busy_timeout=5000 / synchronous=NORMAL / WAL）。切换 = 换进程不换数据。
3. **行为语义对等**：CAS 条件 UPDATE（现存 15 处：hub 11 + tasks 4）、铁律 4（after_commit 顺序 = commit → WS 事件 flush → 回调按注册序）、O9 403 面、幂等键、keyset 分页（cursor 语义）逐一对齐；py 测试 668 例逐用例对应移植。
4. **守门只增不减**：六门全绿才收口；基线重置（批 A pytest / 批 B pytest→server-ts vitest）必须在 CURRENT-HANDOFF 显式登记，比照 DEDAG 先例。
5. **web 零改动**：web 只耦合 8787 端口 + `/api` 前缀 + 生产态静态托管 dist（实测确认零实现耦合）——server-ts 必须承接 dist 静态托管义务。
6. **验证真实性**：实机 verify 用真组件；**verify 断言面 = 覆盖面**（CR-TS 教训：没断言的字段等于没验）。

## §2 质量保障体系（每条有事故/先例背书）

| 机制 | 内容 | 背书 |
| --- | --- | --- |
| **契约先行** | 动 wire 面必先升契约版本；本计划预期全程零修订，出现修订需求即停工上报 | 纪律 1 |
| **校准先行** | 未知运行时语义先写探针拿硬数字，探针全 PASS 才开工（§5.2 scal 清单） | K2-cal、TS1 cal1–cal8 两度成功 |
| **机制守门先于代码** | 新包第一波就配 erasableSyntaxOnly + 导入纪律锚 + 体例 README，不等出事后补 | CR-TS 教训：参数属性族逐点修完才发现机制守门缺位 |
| **py-pydantic 隐形义务显式化** | py 靠 pydantic fail-closed 的每个入口（请求体/WS 帧/daemon 帧），TS 侧必须有生成的运行时校验器，禁止裸 cast 直通 | CR-TS 实锤族：deliver 缺 id 毒化游标、hello_ack NaN |
| **逐用例测试对等** | py 每个 test_* 在 TS 侧有对应例，拆分/增补在文件头登记 | daemon 批 214→247 验收模式 |
| **对拍 oracle** | 迁移期 py server 就是活语义权威：同种子库+同请求序列打双端，规范化响应逐字节 diff（§5.4） | 纪律 8 双跑逐字节守门的放大版 |
| **家族化修复** | 缺陷确认即全库扫同族；并行代理产出的对称模块（如域波次间）收口后跑**对称性审计**专波 | CR-TS 教训：claude/codex 适配器守卫不对称（EPIPE 监听单侧缺失） |
| **批后 CR 评审固定动作** | 每批收口后 /code-review high：多路 finder → 对抗核实（含实机复现）→ 修复 + 回归测试 | CR-TS 实证：一轮抓出 1 critical + 10 major |
| **实机 verify 真组件** | 真 uvicorn/node server × 真 daemon-ts × 真 git × 浏览器 E2E，隔离端口，探针无掩盖性 env，断言面清单化（§5.5） | 纪律 8 + which/PATHEXT 漏网教训 |

## §3 效率保障体系

- **多代理 + Workflow 编排**（daemon 批实证 7.5k 行当日收口）：摸底/校准并行 Workflow → 域波次互斥文件集并行 → 收尾子代理；核心高风险面（Tx/hub 主循环）主编排亲做。
- **波内 pipeline 不设 barrier**：每域「移植→自测→tsc」独立流水，波间才同步守门。
- **体例直接复用**：daemon-ts 的 tsconfig（含 erasableSyntaxOnly）/README 体例七条/导入纪律锚/vitest 配置/win32 校准结论（cal1–cal8）原样搬入 server-ts，不重议。
- **gen 链复用扩展**：contracts.schema.json（224 模型全量 JSON Schema，实测含全部 REST 请求/响应体）已存在，校验器代码生成只是 gen.mjs 加一步。
- **摸底先行已完成**：本文 §5.0 事实包即 B0 产出，批 B 开工直接从裁决确认进入校准。

---

## §4 批 A：py daemon 退役（详细步骤）

**前置**：owner 拍板开闸。**回滚**：任何一步可逆——A1/A2 是配置切换，A3 前打 tag，删码后回滚 = git revert。

| 步 | 动作 | 验收 |
| --- | --- | --- |
| A0 | 建议先修挂账 TS-③（RuntimeAdapter 同名消歧）与 TS-⑥ 中零风险项，减少退役后 TS 侧孤儿注释 | daemon-ts vitest 绿 |
| A1 | 默认启动切换：启动文档/教程/CURRENT-HANDOFF 常用命令改为 `node apps/daemon-ts/src/cli.ts …`（或 bin）；**node ≥22 写明为前置依赖**；py 启动命令降级为「备用」段落 | 文档四处同步；实机起 daemon-ts 连 8787 正常 |
| A2 | **双轨观察期（建议 ≥3 个实用日）**：owner 实机日常使用全走 daemon-ts；观察面 = 真 CLI 会话稳定性 / worktree 链 / 部署链 / 断连重连；期间 py daemon 保持可一键切回 | 无 P1 异常；异常即修（daemon-ts 侧）不回切 |
| A3 | 删除 `apps/daemon`（48 文件）+ workspace 成员/pyproject 清理 + **pytest 基线显式重置 981→~751 收集**（daemon 230 例随退役删除，daemon-ts vitest 270/4 已逐用例承接）+ 探针清点：引用 py daemon 内部件的 scratchpad 脚本改指 TS 或移 archive/ | 六门全绿；CURRENT-HANDOFF §7 基线行重写并登记重置理由 |
| A4 | 文档收口：00-技术选型补记、PROJECT-RECORD 补行、教程截图涉 daemon 启动的两处重摄 | 收口提交 |

**风险登记**：TS-④（oauth_token_refresh 帧不应答）/TS-⑤（凭证单向同步）为 py/TS 同族，退役不加重；若批 A 前修则只修 TS 一侧（省一半工时，建议顺序=先修再退役）。

---

## §5 批 B：server TS 化

### §5.0 摸底事实包（2026-07-19 实测，计划依据）

**结构**：src 60 文件 13,396 行。**`computers/hub.py` 3,413 行 = 25%**（daemon 帧循环 + 十步对账全在内，必须独立成波）。routes 14 模块 4,754 行 / 80 端点；域服务 17 模块 2,819 行；db 1,085 行（models.py 961 = 35 表）；ws/hub.py 239 行（下行 36 事件型 + 上行仅 ping/sub/unsub 3 型）。`canvas/ system_nodes/ templates/` 仅剩 `__pycache__` 死目录（DEDAG 遗留，批 B 前顺手清）。

**数据层（实测）**：SQLAlchemy **Core**（非 ORM Session——无惰性加载/身份映射语义需复刻）；全部 153 个路由 handler 为 **sync def**（Starlette 线程池承载）；hub 侧 DB 写经 `asyncio.to_thread` 下放（L4a 模式）。事务 = `deps.py Tx`：每请求一连接一显式事务，**commit → WS 事件 flush → after_commit 回调按注册序**（铁律 4 的实现锚点，5 处消费：tasks×2/deployments/members/held_drafts）。CAS rowcount 条件 UPDATE 15 处。PRAGMA 四件套。SQL 面 = 可移植 SQL + JSON1 `json_extract`×2 + FTS5 trigram 虚表 + 触发器（0002/0005 迁移建）；**无自定义函数、无窗口函数**。

**node:sqlite 实测（本机 node 22.22.1，SQLite 3.51.2 内置）**：DatabaseSync 全功能通过——条件 UPDATE `.run().changes` CAS 原语 ✓、BEGIN IMMEDIATE/COMMIT/ROLLBACK ✓、WAL/busy_timeout/foreign_keys ✓、JSON1 ✓、**FTS5 trigram ✓**、触发器 ✓、UDF 支持但不需要。**同步阻塞实证**：跨进程写锁 busy-wait 期间事件循环完全冻结（41ms 实测）；无异步变体。加载时打 ExperimentalWarning。

**gen 链（实测）**：`contracts.schema.json` = 224 模型全量 JSON Schema（draft 2020-12，含全部 REST 请求/响应模型，271 $defs，237KB）；`openapi.json` **由 mock app 导出**（65 路径 82 操作，与真 server 集合差异仅 `/__mock/*` vs `/healthz`，其余 63 路径全同）。

**web 耦合（实测）**：仅 vite proxy `'/api'→127.0.0.1:8787, ws:true` + 生产同源静态 dist。**server 换实现对 web 不可见**，但 server-ts 须承接 dist 静态托管。

**迁移脚本（实测）**：13 个 alembic 版本中 **12 个依赖 live model**（`models.Base.metadata.create_all(tables=显式子集)`）+ raw op（FTS5 重建/触发器/反射加列）——**不可机械翻译成 TS**。版本表 = 默认 `alembic_version`；server 启动不自动迁移（测试/生产均外部驱动 upgrade）。

**py 运行时依赖**（退役对象）：fastapi / uvicorn / websockets / sqlalchemy / alembic（alembic 按 D2 保留为工具链）。

### §5.1 六项裁决（预填推荐，开工前 owner 逐项拍板）

| # | 裁决 | 推荐 | 依据与代价 |
| --- | --- | --- | --- |
| **D1** | SQLite 驱动 | **node:sqlite（内置，零依赖）**，同步用法直接在主循环 | 全功能实测通过（含 FTS5/CAS/事务）；本产品单写进程+单用户，查询亚毫秒，同步阻塞仅在**跨进程**锁竞争时发生（alembic CLI/外部工具短暂持锁，罕见且 busy_timeout 兜底）。py 的 to_thread 下放是为多线程池设计，TS 单线程顺序执行天然无进程内竞争。ExperimentalWarning：engines 锁 node ≥22.18 + 启动参数抑制或接受；**逃生门 = better-sqlite3**（API 形状同为同步，切换成本被 §5.3 B2 的 db 单点层封住） |
| **D2** | 迁移体系 | **alembic 留 py 作为工具链**（与 contracts/scripts 同类）；server-ts 启动读 `alembic_version` 与 gen 导出的 `EXPECTED_ALEMBIC_HEAD` 常量比对，**不匹配 fail-closed 拒绝启动** | 12/13 迁移依赖 live model 不可移植；契约批「同批迁移」纪律本就要求迁移与 py 契约同改。代价 = 建新库/升级需 `uv run alembic upgrade head` 一条 py 命令（开发机本就有 uv） |
| **D3** | 运行时依赖策略 | **微集 = {ws}**（8.x，实测零传递依赖），其余零依赖（HTTP 用 node:http 手写路由——80 端点在生成校验器加持下工作量可控） | node 无内置 WS **服务端**（实测确认）；手搓 RFC6455 服务端帧层风险大于一个零传递依赖的包。不引 fastify/hono/express：路由本质是 80 条前缀匹配表 |
| **D4** | 请求/帧运行时校验 | **gen 链扩一步：ajv standalone 编译 contracts.schema.json → `apps/server-ts/src/generated/validators.ts`**（生成物，ajv 仅 devDep，运行时零依赖） | 封死「pydantic 隐形义务」整族缺陷面（CR-TS 实锤模式）；422 错误体形状以 py FastAPI 实响应为 golden 对齐 |
| **D5** | hub 并发模型 | 单线程同步 DB + **报文写保持 L4a 批量化语义但去 to_thread**（顺序处理即串行化）；心跳/PING 活性靠「DB 调用亚毫秒 + 无跨 await 持锁」保障，长事务禁令写进体例 | py 的 to_thread 是线程池产物；TS 侧引 worker_threads 反而制造新的跨线程一致性面。校准 scal3 实测帧循环延迟上限兜底 |
| **D6** | OpenAPI/gen 源 | **mock-server 保留**（契约工具链件：gen REST 源 + conformance 夹具），批 C 归零工作量 | openapi.json 实测由 mock 导出且与真 server 63 路径全同；换源方案（真 server app.openapi()）在 py server 退役后无宿主，mock 留 py 与契约权威同域自洽 |

### §5.2 B-cal 校准先行（scal 探针清单，全 PASS 才开工，比照 cal1–cal8）

| # | 面 | 探针内容 |
| --- | --- | --- |
| scal1 | node:sqlite × 既有库 | 用 alembic 建到 head 的真库，node 侧全表读写 + FTS5 查询 + 触发器行为与 py 侧结果 diff |
| scal2 | CAS 语义 | 双进程并发条件 UPDATE 竞争（changes=0 stale 路径）、BEGIN IMMEDIATE 升级时机、busy_timeout 到期行为（抛 vs 返回） |
| scal3 | 事件循环活性 | 帧循环压力下（daemon 高频 report + 前端 WS 广播）同步 DB 写的最坏延迟分布；PING 按期性 |
| scal4 | ws 包服务端 | upgrade 握手 + Bearer 头透传 + 大帧（32MB 家族上限）+ 背压 + 半关闭；与 daemon-ts 客户端（原生 WebSocket）真互连 |
| scal5 | ajv standalone 生成链 | 224 模型编译产物体积/加载耗时；422 错误细节形状与 py FastAPI golden 对齐可行性 |
| scal6 | 静态托管 + 同源 WS | node:http 托管 web dist + 同端口双 WS 升级路径（/api/ws 与 /api/daemon/ws）共存 |
| scal7 | win32 面 | 复用 cal1–cal8 结论；增量：server 侧文件流（files/store 168 行）大文件 + UTF-8 路径 |
| scal8 | alembic_version 校验门 | gen 导出 EXPECTED_ALEMBIC_HEAD；TS 启动比对 + 不匹配 fail-closed 的 UX（错误话术给出升级命令） |

### §5.3 波次分解（B0 已完成=本文 §5.0；互斥文件集，波间六门）

| 波 | 内容 | 对应 py 面（行数） | 测试对等目标 |
| --- | --- | --- | --- |
| **B1 骨架** | `apps/server-ts` 包（体例复制 daemon-ts）+ node:http 路由器 + 错误单点（ApiError/32 ErrorCode/422 形状）+ auth（owner 隐式 + X-Acting-Member + Bearer sha256）+ 健康端点 + 静态托管 + gen validators 接入 | app/api/deps 骨架面（471） | 错误形状/auth golden ~30 例 |
| **B2 数据层** | db 单点模块（连接/PRAGMA/Tx 事务对象：pending 事件 + after_commit 按序回调）+ 35 表 schema 常量镜像（列名/索引名从 contracts 生成或锁 golden）+ CAS helper 单点（条件 UPDATE→changes 判定）+ keyset 分页 helper + alembic head 校验门 | db/ + deps.Tx + _pagination（~1,300） | Tx 语义例（commit/rollback/顺序）+ 分页 golden |
| **B3 基础域** | workspace / computers / projects / channels / members-agents 路由与服务 | routes 五模块 + role_templates（~1,900） | 对应 py 例逐移 |
| **B4 消息域** | messages / threads / files / read-position / search（FTS5）/ activity / reminders / held_drafts + guard G1–G6 | routes 四模块 + services（~2,600） | 同上（含幂等键例） |
| **B5 任务域** | tasks 全 17 端点 + merge + contracts + silence + ledger + O9 面 | tasks/merge/ledger/guard 消费面（~2,300） | 同上（CAS 4 处逐一锁行为例） |
| **B6 hub-α** | daemon hub 骨架：WS 升级/hello-ack/帧读写循环/ack-reply 配对/report 队列批量写（L4a 去线程化）/PING-PONG | hub.py 前段（~1,200） | 用 daemon-ts 测试侧 transport 反向作 stub |
| **B7 hub-β** | 指令下发面（17 instr 的 server 侧封装）+ report 落库 handlers + deploy/preview/usage 域 + WS 前端桥（36 事件 + sub/unsub 流） | hub.py 中段 + deployments/worktrees/usage routes + ws/hub（~2,900） | 同上 |
| **B8 hub-γ** | **十步对账**（presence/自恢复/投递回填/reminder 重燃/preview 快照重放/deployment fail-close 等）+ 崩溃恢复语义 | hub.py reconcile 段（~1,300） | py 对账例逐移（test_daemon 30 例核心） |
| **B9 收口** | 对拍全量（§5.4）→ 对称性审计专波（域间守卫一致性，CR-TS 教训固定动作）→ 实机 verify（§5.5）→ /code-review high 批 → 基线重置 + 文档 + 双轨切换预案 | — | 六门全绿 + 对拍零 diff |

### §5.4 对拍 oracle 机制（批 B 专属守门，迁移完成后退役）

1. **种子同构**：同一 alembic head 库模板 + m6a_harness 种子，py/ts 各起一实例（隔离端口）。
2. **请求回放**：从 py 测试套提炼**请求脚本集**（每域 20–50 条含边界/错误路径），双端回放；响应做规范化（时间戳/ULID 置换表）后逐字节 diff。
3. **WS 事件流 diff**：同一请求序列触发的前端 WS 事件序列（type+key+data 形状）双端对齐；daemon 面用脚本化 stub daemon 双端等价回放。
4. **纪律**：diff 非空 = TS 侧缺陷（py 是权威），除非能证明 py 行为违反契约文本——那是停工上报面，不是"顺手改对"面。

### §5.5 实机 verify 断言面清单（开工时细化为 T 编号，此处定必答面）

真 server-ts × 真 daemon-ts × 真 git × 真浏览器：启动门（alembic head 校验拒绝面也要探）/auth 三态/消息+线程+文件往返/FTS 搜索中文/任务全生命周期含 CAS 竞争双发/merge 真冲突派回/preview 起停回收/deploy 全链含日志游标与 URL/**detected_runtimes 逐字段断言（which 漏网教训）**/断连 503 面/重启十步对账逐步断言/前端 WS 断线重连/静态托管 + 生产同源。**每面断言具体字段值，不接受「连上了」级断言。**

---

## §6 批 C 裁定：mock-server 保留

裁定（依据 D6）：mock-server = 契约工具链件（gen REST 源 + conformance 双跑夹具），**与 contracts/scripts 同列「不迁清单」**，零工作量。唯一后续动作：批 B 收口时把 `test_conformance_dual` 的「真 server 侧」改为打 HTTP 到 server-ts（mock 侧不动），确保 conformance 双跑在新架构下继续守门。

## §7 节奏建议

```
拍板(§9) → 批A A0-A1（半天）→ A2 双轨观察（≥3 实用日，与批B前期并行）
       ↘ 批B：裁决确认 → B-cal scal1-8（1 当量日）→ B1+B2（1 日）→ B3∥B4∥B5（1 日，三域并行）
          → B6→B7→B8（hub 串行 1–1.5 日，高风险面主编排亲做）→ B9 收口（1 日）
批A A3-A4 在 A2 期满后随时插入（半天）；批C 零工作量随 B9 顺带。
```

关键路径 = 批 B 的 hub 三波（串行，依赖递进）；总关键路径约 **5–6 当量日**。所有并行波次沿用 Workflow 编排 + 互斥文件集纪律。

## §8 风险登记（Top 5）

| 风险 | 等级 | 对策 |
| --- | --- | --- |
| node:sqlite experimental API 漂移 | 中 | D1 逃生门：db 单点层隔离驱动 API，better-sqlite3 一文件切换；engines 锁版本 |
| hub 语义遗漏（3,413 行含十年份教训：L4a/CAS/对账 fail-close） | 高 | hub 三波拆分 + py 例逐移 + 对拍 + 十步对账逐步实机断言；主编排亲做不下放 |
| 同步 DB 阻塞活性（跨进程锁罕见场景） | 低 | scal3 压测拿最坏分布；超标则仅 hub 写路径下放 worker（预留缝，不预建） |
| 422/错误体形状与 FastAPI 微差 | 中 | scal5 golden 先行；对拍全量兜底 |
| 双服务并存期误起双写 | 中 | 8787 端口互斥天然防双起；切换预案写明「先停后起」+ WAL 检查点 |

## §9 拍板点汇总（等 owner）

1. **批 A 开闸**（A1 切默认 + A2 观察期起点）；建议顺带拍 TS-④ 先修（只修 TS 侧省一半）。
2. **批 B 六裁决 D1–D6**（§5.1，均有推荐项；D1/D3 影响最深先拍）。
3. **批 B 立项时点**（建议：批 A 观察期并行启动 B-cal，观察期满且 scal 全 PASS 即开 B1）。

## §10 事实源指针

- 路线总图：[TS-MIGRATION-ROADMAP.md](TS-MIGRATION-ROADMAP.md) · daemon 批裁决/执行：[TS-MIGRATION-HANDOFF.md](TS-MIGRATION-HANDOFF.md) · daemon 实机证据：[../verify/TSDAEMON-VERIFY-EVIDENCE.md](../verify/TSDAEMON-VERIFY-EVIDENCE.md)
- 本文摸底原始面：server 结构图与数据层实测（2026-07-19 两路 scout，数字已内化 §5.0；探针脚本未入库，关键结论全部登记在案）
- 日常状态：[CURRENT-HANDOFF.md](CURRENT-HANDOFF.md)
