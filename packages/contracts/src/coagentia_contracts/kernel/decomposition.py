"""拆解提案同构校验内核（契约 B §12.2 / 拆解设计 §5–§6 的 Python 权威实现）。

规范条文（逐条对应拆解设计 §5.1 schema、§6 V1–V14）：
1. `parse_control`：从消息正文提取**恰好一个** `<control>{…}</control>` 控制块并解析为 JSON 对象。
   缺块 / 多块 / JSON 坏 / 非对象 → 错误码 `CONTROL_PARSE`（V1，含区分性 message）。**围栏容忍**：
   控制块被 markdown 代码围栏（``` 或 ~~~，含语言标注）包裹时仍识别（只按标签定界，围栏字符不参与
   定界）；围栏内外重复出现同一块 → 两块 → 多块错误。
2. `validate_proposal`：V2–V14 **全量收集**（不遇错即停），V1 归 parse_control。返回错误列表
   `[{code, path, message, hint?}]`（空 = 通过），形状即 `coagentia.decomposition-errors.v1` 的
   errors 数组元素；信封（schema/proposal_revision）由 server 侧包。path 用 `$.edges[2].to` 体例。
   只支持安全子集（type/required/enum/const/bounds/additionalProperties，V5 实现纪律）——手写、仅
   标准库，不引入 jsonschema/pydantic。
3. `proposal_fingerprint`：剔除系统注入字段（revision/proposed_by/proposed_at）→ nodes 按 temp_id
   排序、edges 按 (from,to) 排序 → 复用 kernel/fingerprint.fingerprint（契约 A §2 规范化序列化，键
   排序 + null 剔除 + SHA-256）。故"同内容不同书写序"指纹相同（拆解设计 §5.2）。

env 约定（J8 上下文注入契约，供其消费）：env = 纯函数参数注入，内核零 IO——
    {node_limit: int, member_ids: [str], bound_project_ids: [str]}
**ref 语义定案：suggested_owner(member_ref)/project(project_ref) 一律为 id 精确匹配**（server 注入
把成员/项目 ULID id 发给 Orchestrator；名字解析不进内核）。member_ids = 本频道成员 id 集；
bound_project_ids = 已绑定本频道的 Project id 集；node_limit = 频道 decomp_node_limit（V6 上限）。

API 为 M6b 后续留缝：J10 delta 校验将"操作应用基线后调 validate_proposal 重验结果图"——validate 保持
纯函数可复用即可，内核不实现 delta 应用（那是 server 侧构造结果图后再调本函数）。

依赖纪律：仅标准库（03 §3.3）。golden/decomposition.json 判例集为 TS 镜像唯一验收标准（纪律 8）。
"""

import json
import re
from typing import TypedDict, TypeGuard

from coagentia_contracts.constants import SCHEMA_DECOMPOSITION_V1, SCHEMA_TASK_PLAN_V1
from coagentia_contracts.kernel.fingerprint import fingerprint
from coagentia_contracts.kernel.graph import detect_cycle

# ---------------------------------------------------------------- 类型


class Env(TypedDict):
    """校验上下文（server 注入，内核零 IO）——见模块 docstring env 约定。"""

    node_limit: int
    member_ids: list[str]
    bound_project_ids: list[str]


class ValidationError(TypedDict, total=False):
    """errors v1 数组元素形状（hint 可缺）。"""

    code: str
    path: str
    message: str
    hint: str


# ---------------------------------------------------------------- 常量（安全子集值域）

# 系统注入字段：Orchestrator 不生成，校验时忽略、指纹时剔除（拆解设计 §5.1）。
_SYSTEM_INJECTED: frozenset[str] = frozenset({"revision", "proposed_by", "proposed_at"})

# 允许字段集（V3 additionalProperties: false 的安全子集实现，**全部层级**逐级 §5.1）。
# 深层允许集须 ≥ 落地消费严格度：task_plan/AC 层对齐 entities.TaskPlanBody/AcceptanceCriterion
# （ContractModel extra="forbid"）——内核放行的提案落地时 model_validate 不得爆炸（F7/F8 域不变量）。
_TOP_ALLOWED: frozenset[str] = frozenset(
    {"version", "source", "mode", "summary", "nodes", "edges", "merge_plan"}
)
_NODE_ALLOWED: frozenset[str] = frozenset(
    {"temp_id", "title", "kind", "system_action", "command",
     "task_plan", "suggested_owner", "project", "writes_code"}
)
_PLAN_ALLOWED: frozenset[str] = frozenset(
    {"version", "goal", "acceptance_criteria", "defaults_decided", "out_of_scope"}
)
_AC_ALLOWED: frozenset[str] = frozenset({"id", "statement", "verify_by", "verify_ref"})
_EDGE_ALLOWED: frozenset[str] = frozenset({"from", "to"})

# V3 定向别名 hint（拆解设计 §6.1）：分层——top 级与 node 级各自的常见误写。
_TOP_ALIASES: dict[str, str] = {"tasks": "nodes", "dependencies": "edges", "deps": "edges"}
_NODE_ALIASES: dict[str, str] = {
    "id": "temp_id", "name": "title", "owner": "suggested_owner", "assignee": "suggested_owner",
}

_MODES: frozenset[str] = frozenset({"decompose", "single_task"})
_KINDS: frozenset[str] = frozenset({"agent", "system"})
_SYSTEM_ACTIONS: frozenset[str] = frozenset({"merge", "check"})
_VERIFY_BY: frozenset[str] = frozenset({"command", "inspect", "manual"})

_TEMP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 错误码目录（拆解设计 §6，勿发明未列出的码）。
CODE_CONTROL_PARSE = "CONTROL_PARSE"
CODE_BAD_VERSION = "BAD_VERSION"
CODE_UNKNOWN_FIELD = "UNKNOWN_FIELD"
CODE_FIELD_INVALID = "FIELD_INVALID"
CODE_NODE_COUNT = "NODE_COUNT"
CODE_DUP_ID = "DUP_ID"
CODE_DUP_TITLE = "DUP_TITLE"
CODE_EDGE_UNKNOWN_NODE = "EDGE_UNKNOWN_NODE"
CODE_EDGE_SELF = "EDGE_SELF"
CODE_GRAPH_CYCLE = "GRAPH_CYCLE"
CODE_PLAN_MISSING = "PLAN_MISSING"
CODE_AC_INVALID = "AC_INVALID"
CODE_OWNER_NOT_MEMBER = "OWNER_NOT_MEMBER"
CODE_PROJECT_REQUIRED = "PROJECT_REQUIRED"
CODE_PROJECT_UNBOUND = "PROJECT_UNBOUND"
CODE_MERGE_PLAN_MISSING = "MERGE_PLAN_MISSING"
CODE_SYSTEM_NODE_INVALID = "SYSTEM_NODE_INVALID"

_CONTROL_OPEN = "<control>"
_CONTROL_CLOSE = "</control>"


# ---------------------------------------------------------------- parse_control（V1）


def _extract_control_blocks(text: str) -> list[str]:
    """按 `<control>`/`</control>` 标签定界扫描全部完整块（围栏字符不参与定界，故天然容忍围栏）。

    手写扫描（非正则）保证跨语言逐字节一致：每个 `<control>` 配其后最近的 `</control>`，收其间
    原文；无闭合标签的残缺开标签不计为块。
    """
    blocks: list[str] = []
    i = 0
    while True:
        start = text.find(_CONTROL_OPEN, i)
        if start == -1:
            break
        content_start = start + len(_CONTROL_OPEN)
        end = text.find(_CONTROL_CLOSE, content_start)
        if end == -1:
            break  # 残缺开标签：无闭合 → 不计块
        blocks.append(text[content_start:end])
        i = end + len(_CONTROL_CLOSE)
    return blocks


def parse_control(text: str) -> tuple[dict | None, ValidationError | None]:
    """提取恰一个 `<control>` 块并解析为 JSON 对象。成功 → (body, None)；失败 → (None, error)。

    error 形状即 errors v1 元素（code=CONTROL_PARSE，path=`$`，区分性 message）。
    """
    blocks = _extract_control_blocks(text)
    if len(blocks) == 0:
        return None, _err(
            CODE_CONTROL_PARSE, "$",
            "未找到 <control> 控制块；提案正文须恰含一个 <control>{…}</control> 块",
        )
    if len(blocks) > 1:
        return None, _err(
            CODE_CONTROL_PARSE, "$",
            f"发现 {len(blocks)} 个 <control> 控制块；恰需一个（围栏内外重复也算多块）",
        )
    try:
        parsed = json.loads(blocks[0])
    except ValueError:
        return None, _err(CODE_CONTROL_PARSE, "$", "<control> 控制块内不是合法 JSON")
    if not isinstance(parsed, dict):
        return None, _err(CODE_CONTROL_PARSE, "$", "<control> 控制块必须是 JSON 对象")
    return parsed, None


# ---------------------------------------------------------------- validate_proposal（V2–V14）


def _err(code: str, path: str, message: str, hint: str | None = None) -> ValidationError:
    e: ValidationError = {"code": code, "path": path, "message": message}
    if hint is not None:
        e["hint"] = hint
    return e


def _is_str(v: object) -> TypeGuard[str]:
    return isinstance(v, str)


def _is_bool(v: object) -> TypeGuard[bool]:
    return isinstance(v, bool)


def _levenshtein(a: str, b: str) -> int:
    """标准编辑距离（确定性，跨语言一致）——EDGE_UNKNOWN_NODE 近似匹配 hint 用。"""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[lb]


def _nearest(missing: str, candidates: list[str]) -> str | None:
    """最近似 temp_id：最小编辑距离，平手取码点最小（sorted 保证确定性）。"""
    best: str | None = None
    best_d = -1
    for cand in sorted(candidates):
        d = _levenshtein(missing, cand)
        if best is None or d < best_d:
            best = cand
            best_d = d
    return best


def _unknown_node_hint(missing: str, temp_ids: list[str]) -> str:
    if not temp_ids:
        return "提案未声明任何节点 temp_id"
    nearest = _nearest(missing, temp_ids)
    listing = "、".join(temp_ids)
    return f"现有 temp_id：{listing}；是否想引用 '{nearest}'？"


def validate_proposal(body: object, env: Env) -> list[ValidationError]:
    """V2–V14 全量收集校验。返回错误列表（空 = 通过）。纯函数、仅标准库。"""
    errors: list[ValidationError] = []
    if not isinstance(body, dict):
        errors.append(_err(CODE_FIELD_INVALID, "$", "提案必须为 JSON 对象"))
        return errors

    node_limit = env.get("node_limit", 12)
    member_ids = set(env.get("member_ids", []))
    bound_project_ids = set(env.get("bound_project_ids", []))

    # -- V2 version const
    if body.get("version") != SCHEMA_DECOMPOSITION_V1:
        errors.append(_err(
            CODE_BAD_VERSION, "$.version",
            f"version 必须为 '{SCHEMA_DECOMPOSITION_V1}'",
        ))

    # -- V3 top-level 未知字段（插入序遍历，跳过允许集与系统注入字段）
    for key in body:
        if key in _TOP_ALLOWED or key in _SYSTEM_INJECTED:
            continue
        hint = _TOP_ALIASES.get(key)
        errors.append(_err(
            CODE_UNKNOWN_FIELD, f"$.{key}",
            f"未知字段 '{key}'（提案 schema 不接受额外字段）",
            f"是否想用 '{hint}'？" if hint else None,
        ))

    # -- V4 top-level 必填 / 类型 / 边界
    if "source" not in body:
        errors.append(_err(
            CODE_FIELD_INVALID, "$.source", "source 为必填项（本频道 source 任务引用）"))
    elif not _is_str(body["source"]) or body["source"] == "":
        errors.append(_err(CODE_FIELD_INVALID, "$.source", "source 必须为非空字符串"))

    mode = body.get("mode")
    if "mode" not in body:
        errors.append(_err(
            CODE_FIELD_INVALID, "$.mode", "mode 为必填项（decompose 或 single_task）"))
    elif mode not in _MODES:
        errors.append(_err(
            CODE_FIELD_INVALID, "$.mode", "mode 必须为 'decompose' 或 'single_task'"))

    # 长度语义钉死为 Unicode 码点（py len 即码点计数；TS 镜像须用码点计数，非 UTF-16 码元）。
    if "summary" not in body:
        errors.append(_err(CODE_FIELD_INVALID, "$.summary", "summary 为必填项"))
    elif not _is_str(body["summary"]):
        errors.append(_err(CODE_FIELD_INVALID, "$.summary", "summary 必须为字符串"))
    elif len(body["summary"]) > 200:
        errors.append(_err(CODE_FIELD_INVALID, "$.summary", "summary 不得超过 200 字"))

    nodes = body.get("nodes")
    nodes_is_list = isinstance(nodes, list)
    if "nodes" not in body:
        errors.append(_err(CODE_FIELD_INVALID, "$.nodes", "nodes 为必填数组"))
    elif not nodes_is_list:
        errors.append(_err(CODE_FIELD_INVALID, "$.nodes", "nodes 必须为数组"))

    edges = body.get("edges")
    edges_is_list = isinstance(edges, list)
    if "edges" in body and not edges_is_list:
        errors.append(_err(CODE_FIELD_INVALID, "$.edges", "edges 必须为数组"))
    edges_eff: list = edges if edges_is_list else []

    node_list: list = nodes if nodes_is_list else []

    # -- V6 节点数（decompose 2..node_limit；single_task 恰 1 且 edges 必空）
    if nodes_is_list and mode in _MODES:
        n = len(node_list)
        if mode == "single_task":
            if n != 1:
                errors.append(_err(
                    CODE_NODE_COUNT, "$.nodes",
                    f"single_task 模式须恰好 1 个节点，实际 {n} 个",
                ))
            if edges_eff:
                errors.append(_err(CODE_NODE_COUNT, "$.edges", "single_task 模式不得声明依赖边"))
        else:  # decompose
            if n < 2 or n > node_limit:
                errors.append(_err(
                    CODE_NODE_COUNT, "$.nodes",
                    f"decompose 模式节点数须为 2..{node_limit}，实际 {n} 个",
                ))

    # -- 逐节点：V3(node) / V4(node) / V10 / V14 / V11 / V12；并收集 temp_id/title 供 V7/V8
    temp_id_order: list[str] = []  # 声明序去重的字符串 temp_id（V8 hint 列举用）
    temp_id_set: set[str] = set()
    seen_temp: dict[str, int] = {}
    seen_title: dict[str, int] = {}
    any_writes_code = False

    for i, node in enumerate(node_list):
        base = f"$.nodes[{i}]"
        if not isinstance(node, dict):
            errors.append(_err(CODE_FIELD_INVALID, base, "节点必须为对象"))
            continue

        # V3 node 未知字段
        for key in node:
            if key in _NODE_ALLOWED:
                continue
            hint = _NODE_ALIASES.get(key)
            errors.append(_err(
                CODE_UNKNOWN_FIELD, f"{base}.{key}",
                f"未知字段 '{key}'（提案 schema 不接受额外字段）",
                f"是否想用 '{hint}'？" if hint else None,
            ))

        # V4 temp_id
        tid = node.get("temp_id")
        if "temp_id" not in node or not _is_str(tid):
            errors.append(_err(CODE_FIELD_INVALID, f"{base}.temp_id", "temp_id 为必填字符串"))
        elif not _TEMP_ID_RE.match(tid):
            errors.append(_err(
                CODE_FIELD_INVALID, f"{base}.temp_id",
                "temp_id 须匹配 ^[A-Za-z0-9_-]{1,32}$",
            ))
        if _is_str(tid):
            if tid not in temp_id_set:
                temp_id_set.add(tid)
                temp_id_order.append(tid)
            # V7 DUP_ID（首现记 index，再现即报）
            if tid in seen_temp:
                errors.append(_err(
                    CODE_DUP_ID, f"{base}.temp_id",
                    f"temp_id '{tid}' 重复（提案内须唯一）",
                ))
            else:
                seen_temp[tid] = i

        # V4 title（长度 = Unicode 码点，同 summary 注记）
        title = node.get("title")
        if "title" not in node or not _is_str(title):
            errors.append(_err(CODE_FIELD_INVALID, f"{base}.title", "title 为必填字符串"))
        elif len(title) < 1 or len(title) > 80:
            errors.append(_err(CODE_FIELD_INVALID, f"{base}.title", "title 长度须为 1..80 字"))
        if _is_str(title):
            if title in seen_title:
                errors.append(_err(
                    CODE_DUP_TITLE, f"{base}.title",
                    f"title '{title}' 重复（提案内须唯一）",
                ))
            else:
                seen_title[title] = i

        # V4 kind（默认 agent）
        kind = node.get("kind", "agent")
        kind_valid = kind in _KINDS
        if "kind" in node and not kind_valid:
            errors.append(_err(
                CODE_FIELD_INVALID, f"{base}.kind", "kind 必须为 'agent' 或 'system'"))
        eff_kind = kind if kind_valid else None

        # V4 writes_code（默认 false）
        wc = node.get("writes_code", False)
        if "writes_code" in node and not _is_bool(wc):
            errors.append(_err(
                CODE_FIELD_INVALID, f"{base}.writes_code", "writes_code 必须为布尔值"))
        writes_code = wc is True
        if writes_code:
            any_writes_code = True

        # V4 suggested_owner 类型（member_ref | null）
        owner = node.get("suggested_owner")
        owner_present = "suggested_owner" in node and owner is not None
        if owner_present and not _is_str(owner):
            errors.append(_err(
                CODE_FIELD_INVALID, f"{base}.suggested_owner",
                "suggested_owner 必须为成员引用字符串或 null",
            ))

        # V4 project 类型（project_ref | null）
        project = node.get("project")
        project_present = "project" in node and project is not None
        if project_present and not _is_str(project):
            errors.append(_err(
                CODE_FIELD_INVALID, f"{base}.project",
                "project 必须为项目引用字符串或 null",
            ))

        # -- kind 相关：V10（agent） / V14（system + agent 禁 system_action）
        if eff_kind == "agent":
            _validate_agent_plan(node, base, errors)
            if node.get("system_action") is not None:
                errors.append(_err(
                    CODE_SYSTEM_NODE_INVALID, f"{base}.system_action",
                    "agent 节点不得声明 system_action",
                ))
        elif eff_kind == "system":
            _validate_system_node(node, base, errors)

        # -- V11 suggested_owner 成员校验（system 节点已由 V14 禁止，故仅 agent/未知 kind）
        if owner_present and _is_str(owner) and eff_kind != "system":
            if owner not in member_ids:
                errors.append(_err(
                    CODE_OWNER_NOT_MEMBER, f"{base}.suggested_owner",
                    f"suggested_owner '{owner}' 不是本频道成员",
                    "可置为 null 留待认领，或先邀请该成员入频道",
                ))

        # -- V12 writes_code → project 必填且已绑定
        if writes_code:
            if not project_present:
                errors.append(_err(
                    CODE_PROJECT_REQUIRED, f"{base}.project",
                    "writes_code=true 的节点必须指定 project",
                ))
            elif _is_str(project) and project not in bound_project_ids:
                errors.append(_err(
                    CODE_PROJECT_UNBOUND, f"{base}.project",
                    f"project '{project}' 未绑定本频道",
                    "先将该 Project 绑定到本频道，或改用已绑定的 Project",
                ))

    # -- V8 edges 引用存在 + 禁自环（逐边声明序）；V3 未知字段执法到 edge 层（无别名清单）
    if edges_is_list:
        for i, edge in enumerate(edges_eff):
            ebase = f"$.edges[{i}]"
            if not isinstance(edge, dict):
                errors.append(_err(CODE_FIELD_INVALID, ebase, "边必须为对象"))
                continue
            for key in edge:
                if key not in _EDGE_ALLOWED:
                    errors.append(_err(
                        CODE_UNKNOWN_FIELD, f"{ebase}.{key}",
                        f"未知字段 '{key}'（提案 schema 不接受额外字段）",
                    ))
            frm = edge.get("from")
            to = edge.get("to")
            frm_ok = _is_str(frm)
            to_ok = _is_str(to)
            if "from" not in edge or not frm_ok:
                errors.append(_err(CODE_FIELD_INVALID, f"{ebase}.from", "from 为必填字符串"))
            if "to" not in edge or not to_ok:
                errors.append(_err(CODE_FIELD_INVALID, f"{ebase}.to", "to 为必填字符串"))
            if frm_ok and to_ok and frm == to:
                errors.append(_err(CODE_EDGE_SELF, ebase, f"禁止自环（from 与 to 同为 '{frm}'）"))
                continue  # 自环即跳过存在性（同一端点）
            if frm_ok and frm not in temp_id_set:
                errors.append(_err(
                    CODE_EDGE_UNKNOWN_NODE, f"{ebase}.from",
                    f"边引用了不存在的节点 '{frm}'",
                    _unknown_node_hint(frm, temp_id_order),
                ))
            if to_ok and to not in temp_id_set:
                errors.append(_err(
                    CODE_EDGE_UNKNOWN_NODE, f"{ebase}.to",
                    f"边引用了不存在的节点 '{to}'",
                    _unknown_node_hint(to, temp_id_order),
                ))

    # -- V9 全图无环（复用 kernel/graph.detect_cycle，勿重写）
    cycle_edges: list[tuple[str, str]] = [
        (e["from"], e["to"])
        for e in edges_eff
        if isinstance(e, dict) and _is_str(e.get("from")) and _is_str(e.get("to"))
    ]
    cycle_nodes = list(temp_id_order)
    for a, b in cycle_edges:
        if a not in temp_id_set:
            cycle_nodes.append(a)
        if b not in temp_id_set:
            cycle_nodes.append(b)
    cycle = detect_cycle(cycle_nodes, cycle_edges)
    if cycle is not None:
        errors.append(_err(
            CODE_GRAPH_CYCLE, "$.edges",
            f"提案图存在环：{' → '.join(cycle)}",
        ))

    # -- V13 存在 writes_code 节点时 merge_plan 必填
    merge_plan = body.get("merge_plan")
    if "merge_plan" in body and merge_plan is not None and not _is_str(merge_plan):
        errors.append(_err(
            CODE_FIELD_INVALID, "$.merge_plan", "merge_plan 必须为字符串或 null",
        ))
    if any_writes_code and (merge_plan is None or merge_plan == ""):
        errors.append(_err(
            CODE_MERGE_PLAN_MISSING, "$.merge_plan",
            "存在 writes_code 节点时 merge_plan 必填",
        ))

    return errors


def _validate_agent_plan(node: dict, base: str, errors: list[ValidationError]) -> None:
    """V10：agent 节点必有合法 task_plan（goal 非空、AC≥1、每条 verify_by 合法）。

    校验严格度 ≥ 落地消费（TaskPlanBody.model_validate，extra="forbid"、AC 四字段全必填）——
    内核放行的 task_plan 落地时不得爆炸。presence+类型即可，空串不禁（TaskPlanBody 不禁，
    未列出的不发明）。检查顺序：未知字段 → version → goal → AC（逐条：未知字段 → id →
    statement → verify_by → verify_ref）→ defaults_decided → out_of_scope。
    """
    plan = node.get("task_plan")
    if "task_plan" not in node or not isinstance(plan, dict):
        errors.append(_err(CODE_PLAN_MISSING, f"{base}.task_plan", "agent 节点必须包含 task_plan"))
        return
    ppath = f"{base}.task_plan"
    # V3 task_plan 层未知字段（allowed = TaskPlanBody 字段集；深层无别名清单）
    for key in plan:
        if key not in _PLAN_ALLOWED:
            errors.append(_err(
                CODE_UNKNOWN_FIELD, f"{ppath}.{key}",
                f"未知字段 '{key}'（提案 schema 不接受额外字段）",
            ))
    # version 若出现必须为 task-plan v1 常量（TaskPlanBody 带默认的 Literal：缺席合法、错值必炸）
    if "version" in plan and plan["version"] != SCHEMA_TASK_PLAN_V1:
        errors.append(_err(
            CODE_FIELD_INVALID, f"{ppath}.version",
            f"version 必须为 '{SCHEMA_TASK_PLAN_V1}'",
        ))
    goal = plan.get("goal")
    if not _is_str(goal) or goal == "":
        errors.append(_err(CODE_PLAN_MISSING, f"{ppath}.goal", "task_plan.goal 不得为空"))
    acs = plan.get("acceptance_criteria")
    if not isinstance(acs, list) or len(acs) == 0:
        errors.append(_err(
            CODE_AC_INVALID, f"{ppath}.acceptance_criteria",
            "task_plan 须至少包含 1 条验收标准",
        ))
    else:
        for j, ac in enumerate(acs):
            apath = f"{ppath}.acceptance_criteria[{j}]"
            if not isinstance(ac, dict):
                errors.append(_err(CODE_AC_INVALID, apath, "验收标准必须为对象"))
                continue
            # V3 AC 层未知字段（allowed = AcceptanceCriterion 字段集）
            for key in ac:
                if key not in _AC_ALLOWED:
                    errors.append(_err(
                        CODE_UNKNOWN_FIELD, f"{apath}.{key}",
                        f"未知字段 '{key}'（提案 schema 不接受额外字段）",
                    ))
            # AcceptanceCriterion 四字段全必填（presence + 类型）
            if "id" not in ac or not _is_str(ac.get("id")):
                errors.append(_err(CODE_AC_INVALID, f"{apath}.id", "id 为必填字符串"))
            if "statement" not in ac or not _is_str(ac.get("statement")):
                errors.append(_err(
                    CODE_AC_INVALID, f"{apath}.statement", "statement 为必填字符串"))
            if ac.get("verify_by") not in _VERIFY_BY:
                errors.append(_err(
                    CODE_AC_INVALID, f"{apath}.verify_by",
                    "verify_by 必须为 command / inspect / manual 之一",
                ))
            if "verify_ref" not in ac or not _is_str(ac.get("verify_ref")):
                errors.append(_err(
                    CODE_AC_INVALID, f"{apath}.verify_ref", "verify_ref 为必填字符串"))
    # defaults_decided / out_of_scope 若出现必须为字符串数组（TaskPlanBody list[str]，null 也炸）
    for fld in ("defaults_decided", "out_of_scope"):
        if fld not in plan:
            continue
        val = plan[fld]
        if not isinstance(val, list):
            errors.append(_err(
                CODE_FIELD_INVALID, f"{ppath}.{fld}", f"{fld} 必须为字符串数组"))
        else:
            for j, item in enumerate(val):
                if not _is_str(item):
                    errors.append(_err(
                        CODE_FIELD_INVALID, f"{ppath}.{fld}[{j}]",
                        f"{fld} 的元素必须为字符串",
                    ))


def _validate_system_node(node: dict, base: str, errors: list[ValidationError]) -> None:
    """V14：system 节点 system_action 合法、check 必有 command、禁 task_plan/suggested_owner。"""
    action = node.get("system_action")
    if action not in _SYSTEM_ACTIONS:
        errors.append(_err(
            CODE_SYSTEM_NODE_INVALID, f"{base}.system_action",
            "system 节点的 system_action 必须为 'merge' 或 'check'",
        ))
    if action == "check":
        command = node.get("command")
        if not _is_str(command) or command == "":
            errors.append(_err(
                CODE_SYSTEM_NODE_INVALID, f"{base}.command",
                "system_action=check 的节点必须提供 command",
            ))
    if "task_plan" in node and node.get("task_plan") is not None:
        errors.append(_err(
            CODE_SYSTEM_NODE_INVALID, f"{base}.task_plan",
            "system 节点不得包含 task_plan",
        ))
    if "suggested_owner" in node and node.get("suggested_owner") is not None:
        errors.append(_err(
            CODE_SYSTEM_NODE_INVALID, f"{base}.suggested_owner",
            "system 节点不得指定 suggested_owner",
        ))


# ---------------------------------------------------------------- proposal_fingerprint


def proposal_fingerprint(body: dict) -> str:
    """规范化提案指纹（拆解设计 §5.2）：剔系统注入字段 → nodes/edges 排序 → 复用 A §2 指纹。

    - 剔除 revision/proposed_by/proposed_at（系统注入，不进指纹）。
    - nodes 按 temp_id 升序、edges 按 (from,to) 升序——数组语义序，故书写序不影响指纹。
    - 键排序 / null 剔除 / SHA-256 由 kernel/fingerprint.fingerprint 完成（勿重写哈希）。
    """
    cleaned: dict = {k: v for k, v in body.items() if k not in _SYSTEM_INJECTED}
    nodes = cleaned.get("nodes")
    if isinstance(nodes, list):
        cleaned["nodes"] = sorted(
            nodes, key=lambda n: n.get("temp_id", "") if isinstance(n, dict) else ""
        )
    edges = cleaned.get("edges")
    if isinstance(edges, list):
        cleaned["edges"] = sorted(
            edges,
            key=lambda e: (
                (e.get("from", ""), e.get("to", "")) if isinstance(e, dict) else ("", "")
            ),
        )
    return fingerprint(cleaned)
