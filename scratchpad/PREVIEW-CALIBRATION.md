# K2-cal —— M7 预览长驻 dev server win32 实测校准

| 项 | 结论 |
| --- | --- |
| 状态 | ✅ 2026-07-13 真机 5/5 探针通过（`passed: true`，15.4s）；无孤儿泄漏 |
| 环境 | Windows 11 Home China 10.0.26200 / Python `.venv\Scripts\python.exe`（uv） |
| 隔离 | scratch cwd 下起 `<py> -m http.server`（零依赖命令）；随机高端口；每探针 `finally` taskkill /F /T 收尾 |
| 复现 | `uv run python scratchpad/preview_calibration.py`（顶层 `passed: true` 才算通过） |
| 结论用途 | K2（daemon 预览进程域）**照本填空**；对账 #9 的孤儿边角登记归 K3/K8 |

## 1. 校准方法

沿 GIT-CALIBRATION 先例：`asyncio` 子进程（与 daemon 同模型）起长驻 `http.server`，
显式 UTF-8，`taskkill /F /T /PID` 收尾。零依赖命令 = `"<py>" -m http.server %PORT% --bind 127.0.0.1`。
覆盖五组：① 端口获取+PORT 注入+健康检查+HTTP 200；② 同端口双开 win32 行为+缓解手段；
③ taskkill 杀树对孙进程覆盖；④ daemon 崩溃孤儿存活与清理；⑤ 存活监控+坏命令超时。

## 2. 结果总表

| 探针 | 真实结果 | 实现约束（K2 照此填空） |
| --- | --- | --- |
| 端口注入+健康检查 | 分配 61157，`PORT` 经 shell 注入，TCP 可达，HTTP 200 | `create_subprocess_shell` + `env["PORT"]`，命令引用 `%PORT%` |
| **同端口双开** | **OS 不拒绝双绑**（`os_rejects_double_bind=false`）；注册表 20 并发全互异 | **daemon 自持端口唯一性**（进程内 assigned 注册表+锁） |
| taskkill 杀孙 | shell 79032 → python 孙 51844，`/F /T` 连孙杀，端口释放 | 杀树纪律覆盖孙进程，key=shell PID |
| daemon 崩溃孤儿 | 父不杀则孤儿存活；端口反查 PID→taskkill 可清 | 清洁关闭须逐个杀子；硬崩溃孤儿 fail-closed 登记 |
| 存活监控+坏命令 | 坏命令先退出（先于健康超时）；log_tail 捕获「No module named」 | 健康检查 vs `proc.wait()` 竞速 FIRST_COMPLETED |

## 3. 关键行为与坑

### 3.1 端口分配与 PORT 注入
- 取端口 = `bind 127.0.0.1:0 → getsockname()[1] → close`（内核分配）。取后立即交子进程用，
  TOCTOU 窗口小；**自撞由注册表消除**，外部 squatter 残留风险 MVP 单机接受（跨机代理 D §12 #3 预留）。
- **必须用 shell（`create_subprocess_shell`）而非 exec**：① `%PORT%`/`$PORT` 由 cmd.exe 展开；
  ② `npm run dev` 类命令字符串本就是 shell 命令。`env["PORT"]` 注入子进程环境，dev server 亦可读 `process.env.PORT`（约定优于配置，契约 D §5.3 / 裁决 #9）。
- 健康检查 = TCP 连通轮询（校准 0.2s 间隔），生产超时默认 **120s**（契约 D §5.3）；可达即上报 running 携 port。

### 3.2 同端口双开 —— 最关键 win32 坑
- **Windows 的 SO_REUSEADDR 允许同端口双绑成功**（Unix 不允许，需 SO_REUSEPORT）。实测：
  - `http.server.HTTPServer.allow_reuse_address = True`（默认设 SO_REUSEADDR）；Vite/CRA/多数 dev server 同款。
  - 两个带 SO_REUSEADDR 的 socket 双绑同端口 → **SUCCESS**；不带该选项 → errno **10048**（WSAEADDRINUSE）拒绝。
- 后果：并发双 preview.start 若拿到同端口，**第二个 dev server 不会崩溃**，静默双绑致 iframe 路由不确定；
  健康检查（TCP 可达）对两者都通过，无法区分。
- **缓解（K2 必做）**：daemon 进程内 `assigned_ports: set[int]` + `asyncio.Lock`，`pick_free_port`
  结果撞注册表则重取；stop/failed/recycled 时 `release`。**不能靠 OS 拒绝双绑，也不能靠「第二进程崩溃」信号。**
  单 daemon 进程管理所有预览（preview.start key=preview_session_id），进程内注册表即足够。

### 3.3 taskkill /F /T 杀树覆盖孙进程
- `create_subprocess_shell` → cmd.exe（子）→ python http.server（孙）。`taskkill /F /T /PID <cmd_pid>`
  连孙一并杀（输出提及孙 PID），孙进程事后不存活、端口释放。**杀树纪律覆盖孙进程成立**。
- 与 `checks.py:_kill_process_tree` 同款；预览杀树 key = daemon 持有的 shell 进程 PID（`proc.pid`）。

### 3.4 daemon 崩溃孤儿（对账 #9 的 win32 真相）
- **Windows 上 asyncio 子进程不随父进程退出自动死亡**（无 Job Object 时无进程组连带杀）。故：
  - **清洁关闭**（正常退出/SIGTERM）：daemon **必须**有 shutdown handler 逐个 `taskkill /F /T` 预览子进程
    （`CheckRunner.cancel()/wait_closed()` 先例）——清洁重启无孤儿，对账 #9 见 starting/running 无进程即 fail-closed 正确。
  - **硬崩溃**（kill -9/断电）：孤儿 dev server 存活并占端口。对账 #9 置 **failed**（fail-closed，不自动重拉，契约裁决 #11）；
    被占端口由 `pick_free_port` 自然规避（不复用占用端口）；孤儿泄漏接受为 MVP 边角（机器重启/手动清理）。
    可选增强 = daemon 启动时按 `preview_sessions.port` 反查 PID→taskkill，但有杀无关 squatter 风险，**MVP 不做**（登记 K8）。
- 校准澄清：契约 D §4.4 #9 文字「daemon 重启子进程必死」仅在**清洁关闭或树杀**下成立；硬崩溃孤儿是已知边角，
  不影响 #9 的 server 侧动作（置 failed），只影响端口/进程残留（可接受）。

### 3.5 存活监控与坏命令失败态
- 坏命令（不存在模块）立即失败退出 → 健康检查永不可达。**存活监控（`proc.wait()`）与健康检查须并行竞速**
  （`asyncio.wait(FIRST_COMPLETED)`）：进程先退出 → 立即 failed，不必空等 120s 健康超时。
- failed 携 `log_tail` = 进程合流输出尾 **≤2KB**（`checks.py:_bounded_utf8_tail` 复用；契约 A `fail_log_tail`/D preview.status.log_tail）。
  实测坏命令尾含「No module named ...」。
- 正常起后被外力杀（模拟 dev server 自己崩）→ shell `wait()` 返回非零 → 上报 failed 携 log_tail。

### 3.6 健康检查参数默认（实现默认，非协议形状）
| 参数 | 校准值 | 生产默认 | 出处 |
| --- | --- | --- | --- |
| 健康检查轮询间隔 | 0.2s | 0.5s（建议） | 实现默认 |
| 健康检查超时 | 15s（校准短超时） | **120s** | 契约 D §5.3 |
| log_tail 上限 | — | **2KB** | 契约 A v1.0.11 / D preview.status |
| 前端心跳周期 | — | 60s | 契约 B §13.1（面板关即停） |

## 4. K2 实现清单（照本填空）
1. `preview.py` 新建：`PreviewRunner`（照 `CheckRunner` 体例，但**长驻**变体）——preview.start/stop 处理器（自然键=preview_session_id）+ preview.status 上报回调 + shutdown 逐个杀子。
2. 端口分配走进程内 `_PortRegistry`（assigned set + asyncio.Lock），撞则重取；stop/failed/recycled 释放。
3. 用 `create_subprocess_shell` + `env["PORT"]=<分配端口>`；健康检查 TCP 轮询（超时 120s）；健康 vs `wait()` 竞速。
4. 超时/夭折/坏命令 → `taskkill /F /T /PID <shell_pid>` 杀树 → 上报 failed 携 log_tail ≤2KB。
5. preview.start 幂等：已在跑（同 preview_session_id）→ noop + 上报现状端口；preview.stop 幂等：已停/不存在 → noop。
6. daemon shutdown handler：`wait_closed()` 逐个 taskkill 所有活跃预览子进程（清洁关闭无孤儿）。

## 5. 复跑
```
uv run python scratchpad/preview_calibration.py
```
2026-07-13 最终复跑 5/5 `passed: true`，无孤儿残留（校准子进程均 `.venv` python 高端口，finally taskkill 收尾）。
