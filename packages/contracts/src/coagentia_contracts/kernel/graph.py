"""画布图内核：环检测与阻塞派生（契约 A §6 / PRD §4.9 的结构判定）。

规范条文：
1. 有向图；节点即画布节点 id，边即 from→to。纯结构函数，无 IO、无时钟、无随机。
2. `detect_cycle`：存在环则返回构成环的节点 id 路径（自环含单节点），否则 None。
   对输入排序（节点按码点、邻接表按码点）保证跨语言确定性——同图同解。
3. `derive_blocked`：节点 blocked ⇔ 至少一个直接前驱不在 `satisfied`；无前驱的根永不 blocked。
   谁 satisfied（上游完成）由 caller 决定，此处只做结构派生。

依赖纪律：仅标准库（00 §3 澄清 2）。golden/graph.json 判例集为 TS 镜像唯一验收标准。
"""

_WHITE, _GRAY, _BLACK = 0, 1, 2


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
    node_ids: list[str], edges: list[tuple[str, str]], satisfied: set[str]
) -> set[str]:
    """派生 blocked 节点集：节点 blocked ⇔ 至少一个直接前驱不在 satisfied 中。

    纯结构函数——上游"完成"语义由 caller 折进 satisfied；无前驱的根节点永不 blocked。
    """
    preds: dict[str, list[str]] = {n: [] for n in node_ids}
    for a, b in edges:
        if b in preds:
            preds[b].append(a)
    return {n for n, ps in preds.items() if any(p not in satisfied for p in ps)}
