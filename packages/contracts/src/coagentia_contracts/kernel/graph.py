"""画布图内核：环检测与阻塞派生（契约 A §6 / PRD §4.9 的结构判定）。

规范条文：
1. 有向图；节点即画布节点 id，边即 from→to。纯结构函数，无 IO、无时钟、无随机。
2. `detect_cycle`：存在环则返回构成环的节点 id 路径（自环含单节点），否则 None。
   对输入排序（节点按码点、邻接表按码点）保证跨语言确定性——同图同解。
3. `derive_blocked`：节点 blocked ⇔ 至少一个直接前驱不在该节点适用的 satisfied 集；无前驱的根永
   不 blocked。**W9 双档 satisfied（M8b L7）**：每个节点按其 `policy` 选用判据——
   `strict`（默认）看 `done_satisfied`（上游 Done/success，现状语义原样）、`partial` 看
   `terminal_satisfied`（上游到达终态即可，含 closed/failed——防单点卡死全 DAG）。谁 satisfied 及
   节点 policy 由 caller 折进入参，此处只做结构派生。签名向后兼容：省略 terminal/policy ≡ 全 strict
   （单集合语义，既有 3 参调用逐字节不变）。

依赖纪律：仅标准库（00 §3 澄清 2）。golden/graph.json 判例集为 TS 镜像唯一验收标准。
"""

_WHITE, _GRAY, _BLACK = 0, 1, 2
# W9 partial 档字面量（= enums.UpstreamPolicy.PARTIAL 值；内核守"仅标准库"纪律不 import 枚举）。
_PARTIAL = "partial"


def _adjacency(node_ids: list[str], edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    """构建邻接表：节点集 = node_ids ∪ 边端点；邻接按码点升序保证确定性。"""
    adj: dict[str, list[str]] = {n: [] for n in node_ids}
    for a, b in edges:
        adj.setdefault(a, [])
        adj.setdefault(b, [])
    for a, b in edges:
        adj[a].append(b)
    for n in adj:
        adj[n].sort()
    return adj


def detect_cycle(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str] | None:
    """有向图三色 DFS 检测环：返回构成环的节点 id 路径（从环入口到回边源），无环返回 None。

    起点按码点升序遍历、邻接按码点升序——保证同一图的返回路径唯一确定（跨语言镜像可比）。
    """
    adj = _adjacency(node_ids, edges)
    color = dict.fromkeys(adj, _WHITE)

    for start in sorted(adj):
        if color[start] != _WHITE:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        path: list[str] = [start]
        color[start] = _GRAY
        while stack:
            node, i = stack[-1]
            if i < len(adj[node]):
                stack[-1] = (node, i + 1)
                nxt = adj[node][i]
                if color[nxt] == _GRAY:  # 回边 → 命中环，从 path 里 nxt 处截到栈顶
                    return path[path.index(nxt):]
                if color[nxt] == _WHITE:
                    color[nxt] = _GRAY
                    stack.append((nxt, 0))
                    path.append(nxt)
            else:  # 邻接耗尽，节点出栈染黑
                color[node] = _BLACK
                stack.pop()
                path.pop()
    return None


def derive_blocked(
    node_ids: list[str],
    edges: list[tuple[str, str]],
    done_satisfied: set[str],
    terminal_satisfied: set[str] | None = None,
    policy: dict[str, str] | None = None,
) -> set[str]:
    """派生 blocked 节点集（W9 双档 satisfied，M8b L7）：节点 blocked ⇔ 至少一个直接前驱不在该
    节点适用的 satisfied 集中；无前驱的根节点永不 blocked。

    - `done_satisfied`：strict 判据集（上游 Done/success，现状语义）。
    - `terminal_satisfied`：partial 判据集（上游到达终态，含 closed/failed）。省略 → 视同
      `done_satisfied`（无 partial 节点时不参与，纯 strict 回归；即使误传 partial 也退化为更严）。
    - `policy`：`{node_id: 'strict'|'partial'}`，缺席节点默认 strict。放行档是**被评估节点**（下游）
      的属性——决定它对自身前驱集合的宽严，非前驱属性。

    纯结构函数——上游"完成"语义与节点 policy 由 caller 折进入参；此处只做结构派生。
    """
    terminal = done_satisfied if terminal_satisfied is None else terminal_satisfied
    pol = policy or {}
    preds: dict[str, list[str]] = {n: [] for n in node_ids}
    for a, b in edges:
        if b in preds:
            preds[b].append(a)
    blocked: set[str] = set()
    for n, ps in preds.items():
        sat = terminal if pol.get(n) == _PARTIAL else done_satisfied
        if any(p not in sat for p in ps):
            blocked.add(n)
    return blocked
