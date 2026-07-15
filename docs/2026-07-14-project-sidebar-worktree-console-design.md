# PS-WT 批设计:项目侧栏(Claude 式)+ 工作树管理台

> 2026-07-14 头脑风暴定稿。流程定位:**独立设计批,暂不立项 M9**——本文自含可直接开工;若日后立项,本文可原样作 M9a 块设计输入。
> 状态:设计定稿待 owner 终审;实施未开始。

## 0. 一句话

两个功能:**① 侧栏「项目」区**(Claude.ai 式项目→频道分组导航 + 新建项目弹窗内嵌远端文件夹选择器,替代手敲 repo_path)+ **② 工作树管理台**(独立顶级屏,列全部任务 worktree,孤儿/丢失漂移态浮出,只读+清理)。**零迁移、零新 WS 事件**,契约面 = D 新增 2 个 QueryType + B 新增 4 个端点。

## 1. 头脑风暴裁决记录(owner 拍板)

| # | 议题 | 裁决 |
|---|---|---|
| 1 | ①的场景 | 选 repo_path(非 IDE 式浏览仓库文件) |
| 2 | ②的语义 | worktree 管理台(纯可见面+运维,不改「一任务一树自动派生」内核,非 wmux 式会话切换) |
| 3 | 浏览范围 | **盘符级全盘浏览**(体验优先,root=null 返回盘符列表) |
| 4 | ②写操作边界 | **只读+清理**;merge 永不入管理台(状态机单一入口不被绕过) |
| 5 | ②位置 | 独立顶级屏(仿 ComputersScreen,Rail 入口) |
| 6 | ①入口形态 | **侧栏「项目」区**,Claude.ai 式:项目组展开=绑定频道;区头＋=新建项目;项目行＋=项目下新建频道并自动绑定 |
| 7 | 管理台数据流 | **方案 A:DB 骨架 + 实时对账 enrich**(与对账 #9 哲学同构) |
| 8 | 流程定位 | 先出设计,暂不立项 M9 |
| 9 | 选择器可选非 git 目录 | 允许,仅黄条提示不阻止(裸目录初始化场景保留) |
| 10 | active 行清理按钮 | 永不显示(哪怕任务卡住——走任务流程) |

## 2. 已核事实(现状地基,开工前无需再摸)

- **契约 D §6 查询代理已存在**:`QueryType.HOME_TREE/HOME_FILE/GIT_DIFF`(contracts/daemon.py:63-68),daemon 侧 `handle_query` 查询-回复帧 + `_safe_join` 防逃逸(apps/daemon/client.py:379-439)。本批为同族扩展。
- **worktrees 表登记面完整**:workspace_id/project_id/task_id(unique)/branch/path/status(active/merged/conflicted/cleaned)/merge_commit/created_at/merged_at/cleaned_at(server/db/models.py:822)。**零迁移**。
- **`worktree.updated` WS 事件已存在**,wsBridge 已消费(web/data/wsBridge.ts:97-105)。**零新事件**。
- **清理命令已存在**:`CommandType.WORKTREE_CLEANUP`(contracts/daemon.py:55,M6),daemon WorktreeManager 有 add/remove/merge/diff(apps/daemon/git.py)。
- **worktree 目录布局规整**:`worktrees_dir/{project_id}/{task_id}`(apps/daemon/paths.py:59)→ 孤儿可用 **(project_id, task_id) 二元组**定位,server 永不下发裸路径。
- **项目 REST 面已完备**:`GET /projects` **已带 channel_ids**、`POST /projects`(admin 门 + `_validate_repo_path`)、`PATCH/DELETE`、频道绑定/解绑端点全在(server/routes/projects.py)。①的后端读写面几乎零新增。
- 前端先例:HomeTab 单层平铺目录列表(AgentDetailScreen.tsx:358)、DiffCard(base..head)、NewChannelModal(M8c)、ComputersScreen 顶级屏形态。
- repo 在 Computer(daemon 所在机)上 → 浏览必须走 daemon 查询代理,浏览器原生目录选择器方向性错误。

## 3. 契约面改动(契约先行,W0 一次落笔)

### 3.1 契约 D:QueryType 新增 2 项(§6 查询代理,只读)

```python
class QueryType(StrEnum):
    HOME_TREE = "home.tree"
    HOME_FILE = "home.file"
    GIT_DIFF = "git.diff"      # M6
    FS_TREE = "fs.tree"        # PS-WT:computer 级目录浏览
    WORKTREE_SCAN = "worktree.scan"  # PS-WT:worktrees_dir 实时扫描
```

请求/回复模型(contracts/daemon.py 同文件新增):

```python
class FsTreeQuery(BaseModel):
    path: str | None          # None → 根视图(win32 盘符列表 / posix 单条 "/")

class FsTreeEntry(BaseModel):
    name: str                 # 显示名(盘符视图 = "C:\\" 等)
    path: str                 # 绝对路径(回传给下一层查询)
    has_git: bool             # 存在 .git(目录或文件,worktree 的 .git 是文件)
    denied: bool              # 无权限进入(行置灰不可点)

class FsTreeReply(BaseModel):
    entries: list[FsTreeEntry]   # 仅目录,不列文件;按名排序
    truncated: bool              # 超上限截断(单层上限 500)

class WorktreeScanQuery(BaseModel):
    pass                      # 无参:daemon 只扫自己的 worktrees_dir

class WorktreeScanEntry(BaseModel):
    project_id: str           # 取自目录层级 worktrees_dir/{project_id}/{task_id}
    task_id: str
    path: str
    branch: str | None        # git 解析失败→None,error 说明
    head_commit: str | None
    dirty: bool               # 工作区有未提交改动
    behind: int | None        # 相对主仓库当前 HEAD 分支落后提交数
    ahead: int | None
    error: str | None         # 单树 git 失败不炸整扫,逐条降级

class WorktreeScanReply(BaseModel):
    entries: list[WorktreeScanEntry]
```

语义与护栏:
- FS_TREE **纯只读列目录,永不读文件内容**;单条目异常(权限/IO)→ `denied=true` 或跳过,永不因一个目录炸整层;junction/符号链接只列不跟(单层查询天然无递归)。
- WORKTREE_SCAN 只暴露 CoAgentia 自管目录,不碰主仓库其他部分;ahead/behind 基线 = **主仓库当前 HEAD 分支**,detached 时置 null。
- 查询超时 → 既有 DAEMON_OFFLINE 语义不变。

### 3.2 契约 B:REST 新增 4 端点(全部人类-only)

| 端点 | 方法 | 权限 | 语义 |
|---|---|---|---|
| `/api/computers/{computer_id}/fs?path=` | GET | admin | 代理 FS_TREE;path 缺省=根视图。503 DAEMON_OFFLINE |
| `/api/worktrees?live=0\|1` | GET | 成员 | 管理台列表;live=1 附实时对账(见 §5.2) |
| `/api/worktrees/{worktree_id}/cleanup` | POST | admin | 清理登记树;409 见 §5.3 |
| `/api/computers/{computer_id}/worktrees/cleanup-orphan` | POST | admin | 清理孤儿;body `{project_id, task_id}`(**ids only,永不传路径**) |

`GET /api/worktrees` 响应形状(rest.py):

```python
class WorktreeLive(BaseModel):
    dirty: bool
    behind: int | None
    ahead: int | None
    head_commit: str | None

class WorktreeConsoleItem(BaseModel):
    id: str | None            # None = 孤儿行(无 DB 登记)
    project_id: str
    project_name: str
    computer_id: str
    task_id: str | None       # 孤儿行 = 目录名解析出的 task_id(可能已被删任务)
    task_title: str | None
    channel_id: str | None    # 跳转 ThreadPanel 用
    branch: str | None
    path: str
    status: str | None        # WorktreeStatus;孤儿行 = None
    derived: str              # "ok" | "missing" | "orphan"(合账派生态,见 §5.2)
    merge_commit: str | None
    created_at: str | None
    merged_at: str | None
    cleaned_at: str | None
    live: WorktreeLive | None # live=0 或该机扫描失败 → None

class WorktreeScanStatus(BaseModel):
    computer_id: str
    status: str               # "ok" | "offline" | "timeout"

class WorktreeConsoleReply(BaseModel):
    items: list[WorktreeConsoleItem]
    scans: list[WorktreeScanStatus]   # live=0 → 空列表
```

- **Agent 身份调用任一新端点 → 403(O9 同门),不注册 MCP 工具。**
- contracts-ts 镜像同步,fixtures + 契约对照测试同波落笔,gen 确定性守门照旧。

### 3.3 迁移 / WS

**迁移:零**(worktrees/projects/channel_projects 现状够用;孤儿不入库,纯 live 展示——登记面只登记自己派生的,磁盘漂移归对账)。**WS:零新事件**(复用 `worktree.updated`;wsBridge 加一条失效规则 → 管理台 query key)。

## 4. daemon 实现(W1)

- `handle_query` 分支 +2:`FS_TREE` / `WORKTREE_SCAN`(client.py 查询代理节,与 `_home_tree` 并列)。
- **FS_TREE**:root 视图 win32 用盘符枚举(逐盘 `Path(f"{c}:\\").exists()` 探测或 `os.listdrives()`,注意 py 版本),posix 返回 `[{name:"/",path:"/"}]`;子层 `iterdir()` 仅收目录,`has_git` = `(child / ".git").exists()`,PermissionError → `denied=true`;>500 条截断置 `truncated`。
- **WORKTREE_SCAN**:遍历 `paths.worktrees_dir` 两层(project_id/task_id),每树:`git rev-parse --abbrev-ref HEAD`(branch)、`git status --porcelain` 非空(dirty)、`git rev-list --count` 双向(ahead/behind,基线=主仓库 HEAD 分支);单树 git 失败 → `error` 字段降级,不炸整扫。
- **孤儿清理**:复用 `WORKTREE_CLEANUP` 命令处理链;daemon 用 `paths.worktree_dir(project_id, task_id)` 自拼路径 → **resolve 后必在 worktrees_dir 内**(双保险,尽管 ids 传参已天然锁定);分支删除仅限 `coagentia/` 前缀;目录已不存在 → 幂等成功。win32 文件锁删除失败 → 明确错误上报,不半删(现有 WORKTREE_CLEANUP 行为核对,见 §9 待确认 #1)。

## 5. server 实现(W2)

### 5.1 fs 代理

`GET /computers/{cid}/fs` → require_admin → hub 查该 computer 在线连接 → FS_TREE 查询帧(既有查询代理超时机制)→ 透传 reply。离线/超时 → 503 DAEMON_OFFLINE(既有错误码)。

### 5.2 管理台列表与合账(本批唯一非平凡逻辑,单独模块 `worktrees/console.py`)

1. **骨架**:worktrees × tasks(title/channel_id)× projects(name/computer_id)一次 join,全部 DB 行(含 cleaned,前端折叠)。
2. **live=1**:对该 workspace 涉及的每个 computer 并发发 WORKTREE_SCAN(单机超时沿用既有查询超时);离线/超时 → 该机 `scans` 条目标记,该机行 `live=None`。
3. **合账规则**(key = `(project_id, task_id)`):

| DB 行 | 磁盘条目 | 结果 |
|---|---|---|
| 有(active/merged/conflicted) | 有 | `derived="ok"` + live 字段 |
| 有(active) | 无 | `derived="missing"`(丢失,worktree 该在而不在) |
| 有(merged/conflicted/cleaned) | 无 | `derived="ok"`(终态无树是正常形态) |
| 无 或 已 cleaned | 有 | 追加孤儿行 `derived="orphan"`, `id=null`(cleaned 有树 = 清理漂移,同样按孤儿浮出) |

合账在 **server 侧**完成(单处逻辑、可单元测试穷举矩阵),前端拿到即渲染。

### 5.3 清理端点

- `POST /worktrees/{id}/cleanup`:门 = ① require_admin ② status ∈ {merged, conflicted} 否则 **409 rule=worktree_not_terminal**(active 永拒,裁决 #10)③ 该任务无活跃 PreviewSession 否则 **409 rule=preview_active**(win32 文件锁预防,复用 M7a 活跃谓词)④ daemon 在线否则 503。通过 → 下发 WORKTREE_CLEANUP → 成功后**条件 UPDATE**(`status IN ('merged','conflicted') → 'cleaned'` + cleaned_at,CAS 纪律,并发第二发 409)→ 既有 `worktree.updated` 广播。
- `POST /computers/{cid}/worktrees/cleanup-orphan`:门 = ① require_admin ② `(project_id, task_id)` **无非 cleaned DB 行**否则 409(防把登记树当孤儿删)③ daemon 在线。通过 → 同命令下发;无 DB 行可写 → 不产生 worktree.updated,响应即终态,前端以响应刷新。
- **锁纪律(CR-M8 教训同族:跨进程同步等待不得跨持锁事务)**:三段式——① 门校验用只读短事务并结束;② 下发 WORKTREE_CLEANUP 并等待 daemon 结果,**期间不持任何写事务**;③ 成功后另开事务做条件 UPDATE + 广播。下发失败/超时 → DB 未动,响应 503,无幽灵态;daemon 成功但 ③ 前崩溃 → 漂移态下次扫描浮出(§7 对账自愈)。
- 全部新端点:acting_member 为 Agent → 403(O9 同门)。

## 6. web 实现(W3=①,W4=②,两波可并行)

### 6.1 侧栏「项目」区(ChannelList.tsx 内新分组,置于频道分组上方)

```
项 目 ───────────────── ＋      ← 区头＋ = NewProjectModal
▼ coagentia              ＋      ← 行＋ = 该项目下新建频道(NewChannelModal 复用+提交后 bind)
    # realtest                   ← 绑定频道引用,点击跳频道
▶ another-repo           ＋
频 道 ─────────────────          ← CHANNELS 主列表原样不动
```

- 数据 = 既有 `GET /projects`(**已带 channel_ids**,零新读端点)+ 频道名从既有频道缓存映射。
- 项目组内频道是**第二入口(引用)**:多对多绑定的频道在多个组出现,CHANNELS 主列表不搬家不消失。
- 项目行点击 = 展开/收起;hover 尾部齿轮 → 跳既有项目设置区。
- `GET /projects` 现为 admin 门 → **项目区仅 admin 渲染**(非 admin 无此区,不放宽读面;见 §9 待确认 #2)。
- 建频道自动绑定 = `POST /channels` → `POST /channels/{cid}/projects/{pid}`(两端点均既有)顺序两发;第二发失败 → toast「频道已建,绑定失败」+ 频道照常出现在 CHANNELS(无孤儿副作用)。

### 6.2 NewProjectModal(①落点)

字段:名称 / Computer 下拉 / 仓库路径(文本框 + **「浏览…」按钮** → FolderPickerModal)。dev/deploy 命令等高级项不进弹窗(Claude 式轻量,建后去设置区补)。提交 = 既有 `POST /projects`。「浏览…」在未选 Computer 时禁用 + tooltip。

### 6.3 FolderPickerModal(共用组件,双入口:NewProjectModal + ProjectSettingsSection「浏览…」)

- 面包屑 + 单层目录列表(每导航一发 `GET /computers/{cid}/fs`,react-query key=`(cid,path)`,回退秒开);根视图 = 盘符列表。
- `has_git` 行加 ⎇ 徽标;`denied` 置灰不可进;`truncated` → 列表底「已截断,可手动输入更深路径」。
- **允许选任意目录**;选中非 git 目录 → 黄条提示「该目录不是 git 仓库」不阻止(裁决 #9)。
- 确认回填 repo_path 文本框,**文本框保持可手改**(网络盘等全盘浏览覆盖不到的场景兜底)。
- daemon 离线 → 弹窗内联提示,手输兜底始终可用。
- 不做:文件显示、搜索、新建文件夹、多选(YAGNI)。

### 6.4 WorktreesScreen(②,独立顶级屏)

- Rail 加 GitBranch 图标入口,路由 `/worktrees`(单 workspace MVP,同 ComputersScreen 形态)。
- 按项目分组;行 = 分支名 / 状态徽标(4 DB 态 + 派生态 **丢失/孤儿**)/ 任务标题链接(channel_id+task_id 跳 ThreadPanel)/ live 列(落后度、dirty;无扫描 → 「—」)/ 时间。
- 行展开 → **复用 DiffCard**(base..head,零新 diff 逻辑)。
- `cleaned` 折叠进「已清理 (n)」区。
- **清理按钮仅 merged/conflicted 行与孤儿行**;确认弹窗**明列将删目录绝对路径+分支名** → POST → 更新。
- 顶部:每机扫描状态条(ok/offline/timeout)+「重新扫描」按钮。
- 节奏:进屏 `live=0` 秒出骨架 → 自动跟发 `live=1` 补 live 字段与孤儿 → 手动「重新扫描」再触发;不轮询(`worktree.updated` WS 失效兜底)。
- wsBridge:`worktree.updated` 失效规则 + 管理台 query key(qk.worktreesConsole)。

## 7. 错误处理汇总

| 场景 | 行为 |
|---|---|
| daemon 离线(浏览) | 选择器内联提示,手输兜底 |
| daemon 离线(管理台) | 该机行保 DB 态,live=「—」,清理禁用+tooltip,状态条标记 |
| 目录权限拒绝 | 逐条 denied 置灰,不炸整层 |
| 单树 git 失败 | 逐条 error 降级,不炸整扫 |
| 清理时预览活跃 | 409 preview_active「先停止预览」 |
| 清理并发 | CAS 条件 UPDATE,第二发 409 |
| 目录已删再清理 | daemon 幂等成功,登记态照常推进 |
| daemon 删成功 server 写失败 | 无补偿;下次扫描以漂移态浮出(对账自愈,不做分布式事务) |
| Agent 调新端点 | 403(O9 同门) |

## 8. 测试面

- **契约对照**:2 QueryType + 4 端点形状进 fixtures;contracts-ts 镜像;gen 确定性。
- **daemon 单元**:FS_TREE(win32 盘根/posix 根/denied/截断/junction 不跟);WORKTREE_SCAN(空/正常/孤儿/dirty/ahead-behind/单树失败降级);清理护栏(worktrees_dir 外拒绝、`coagentia/` 前缀、已删幂等)。
- **server 单元**:合账矩阵全象限(§5.2 表 4 行 × 机器在线/离线);cleanup 门四连(not_terminal/preview_active/CAS 并发/agent 403);fs 代理离线 503。
- **web vitest**:项目区(admin 门/展开/引用频道/双入口计数);NewProjectModal;FolderPickerModal(导航/回退缓存/回填/非 git 黄条/截断);WorktreesScreen(分组/派生态徽标/清理确认文案含路径/离线禁用)。
- **实机 verify 脚本**(`scratchpad/pswt_verify.py`,项目纪律):真 uvicorn + 真 daemon:V1 FS_TREE 真盘符;V2 浏览选文件夹建项目全链;V3 项目下建频道自动绑定;V4 造孤儿目录→扫描浮出→清理消失;V5 merged 树清理 CAS+WS 反流;V6 active 行无清理面;V7 预览活跃 409;V8 agent 403;V9 断 daemon 降级面;V10 浏览器 E2E console 零错误。

## 9. 待确认项(开工首日核,不阻塞设计定稿)

1. **WORKTREE_CLEANUP 现有行为**:对无 DB 登记的 (project_id, task_id) 下发时,daemon 的 worktree.status 上报与 server `_report_worktree_status` 是否安全 no-op;win32 文件锁删除失败的上报形状。
2. **项目区可见性**:默认随 `GET /projects` 的 admin 门(仅 admin 见);若 owner 想全员可见需同步放宽该端点读面(单独小裁决)。
3. **`_validate_repo_path` 兼容**:选择器回填的绝对路径(含尾分隔符/盘符大小写)须过既有校验,不兼容则以校验为准修回填格式。
4. **ahead/behind 基线**:主仓库 detached HEAD 时置 null 的 UI 文案。
5. **os.listdrives() 可用性**(py3.12+):daemon 支持的最低 py 版本若低于 3.12,用逐盘探测替代。

## 10. 实施波序(建议,非立项承诺)

| 波 | 内容 | 依赖 |
|---|---|---|
| W0 | 契约落笔(D+B+TS 镜像+fixtures+对照测试)+ §9 五项核毕 | — |
| W1 | daemon:FS_TREE / WORKTREE_SCAN / 孤儿清理护栏 + 单元 | W0 |
| W2 | server:fs 代理 / 合账模块 / 清理两端点 + 单元 | W0(与 W1 可并行,mock 帧) |
| W3 | web-①:项目区 + NewProjectModal + FolderPickerModal + vitest | W0(mock 契约可先行) |
| W4 | web-②:WorktreesScreen + Rail + wsBridge + vitest | W0(同上) |
| W5 | 实机 verify 10 项 + 浏览器 E2E + /code-review | W1-W4 |

守门照旧:pytest 全绿 / vitest 全绿 / pyright 0 / ruff 净 / gen 确定 / build 绿。
