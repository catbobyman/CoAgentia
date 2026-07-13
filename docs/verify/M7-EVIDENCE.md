# M7 实机 verify 证据

> 真 uvicorn + 真 websockets daemon-sim（真 git.py + **真 PreviewRunner 起真 dev server 子进程**）+ 真 scratch git 仓库。隔离临时库（alembic head 0010 + seed）、独立端口、taskkill 杀树收尾。脚本可重跑；结论截图/数字归本目录。

## 块 M7a「预览链」—— 14/14 ALL PASS（2026-07-13，Fable 亲跑）

脚本 = [`scratchpad/m7a_verify.py`](../../scratchpad/m7a_verify.py)（appfactory = `m7a_appfactory.py` 短回收间隔）；结果 = [M7A-VERIFY-results.json](M7A-VERIFY-results.json)。dev_command 用零依赖 `python -m http.server %PORT%`（K2-cal 裁决），worktree 内真 http 服务，iframe 数据源真实 HTTP 200。

| # | 场景（§9a #7） | 判据 | 结果 |
| --- | --- | --- | --- |
| P0.1 | daemon-sim 真 websockets 连上真 server | connected 事件 | ✅ |
| P0.2 | 两 Project 建立并绑定频道（好/坏 dev_command） | 201 + 绑定 | ✅ |
| **P1.1** | writes_code 任务激活联动**真 git 派生 worktree** | TaskDetail.worktree 行 + 物理目录 | ✅ |
| **P1.2** | POST /tasks/{id}/preview = **201 建 starting 会话** | status=starting | ✅ |
| **P1.3** | daemon **真起 dev server → 健康检查 → running 携 port** | preview.status running + port | ✅ port=58740 |
| **P1.4** | **iframe 数据源真实可达 HTTP 200**（真 http.server 在 worktree） | httpx GET 127.0.0.1:{port} = 200 | ✅ |
| **P2.1** | 并排双预览**端口互异**（注册表唯一性，win32 SO_REUSEADDR 缓解实证） | port1 ≠ port2 | ✅ 58740 vs 55466 |
| P2.2 | 两预览 iframe 同时 HTTP 200 | 双 200 | ✅ |
| **P3.1** | 二次 POST = **200 touch 同会话**（ensure+touch 幂等） | 200 + same session_id | ✅ |
| **P4.1** | **idle 超时 → 回收扫描下发 stop → recycled**（backdate last_active_at 触发） | status=recycled | ✅ |
| **P4.2** | 回收后 **dev server 子进程被杀**（端口不可达） | TCP connect refused | ✅ |
| **P5.1** | **坏 dev_command → failed** | status=failed | ✅ |
| **P5.2** | failed 携 **fail_log_tail**（进程输出尾） | 含 "No module named" | ✅ |

**孤儿核验**：测后 `.venv python http.server` 残留 = **0**（清洁关闭 wait_closed 逐个杀子）。

### 覆盖的 PRD M7a 出口句（交互 §12 / FR-11）
- 「交付 → Diff/预览验收」的**预览**环节：真机打开 dev server（健康检查 → running）+ iframe 真实可达（HTTP 200）✅
- 「并排多任务对比」（FR-11.2）：双预览端口互异、同时可达 ✅
- 「回收三触发」之 idle 超时（FR-11.3）：自动 stop + 进程杀树 ✅
- 「启动失败显示日志尾」（交互 §12）：坏命令 failed 携 fail_log_tail ✅
- ensure+touch 幂等（B §13.1 裁决 #8）：二次 POST=touch 同会话 ✅

### 不变量实证（Fable 亲审 K2/K3 的对抗核对在真机复现）
- **端口唯一性靠注册表不靠 OS**（K2-cal 最关键 win32 坑）：P2.1 双预览端口互异。
- **CAS 状态机边写**（K3）：running/recycled/failed 转移经条件 UPDATE，P1.3/P4.1/P5.1 各状态如期推进。
- **commit-then-dispatch 硬保证**（Fable 修）：P1.3 running 帧 CAS 命中已提交 starting 行（无丢帧）。
- **杀树覆盖孙进程 + 清洁关闭无孤儿**：P4.2 端口不可达 + 孤儿核验 0。

> 块 M7a 的浏览器可视 E2E（预览面板三态顶条 / 并排面板 / 心跳）与部署卡一并在 **K9（PRD M7 出口）** 的完整流程（需求消息 → 拆解 → 交付卡 → 预览验收 → 合并 → 部署）中截图归档——交付卡承载预览按钮，需完整 message→task 链，落 K9 最自然。M7a 块级以本真机链 14/14 收口。

## 块 M7b「部署、成本与收尾」

（待 K9 收口填充：需求消息 → 拆解 → 执行 → Diff/预览验收 → 合并 → 一键部署 URL + 新账 token 小结；对账 #9/#10 崩溃探针。）
