"""拆解校验内核测试（V1–V14 + <control> 解析 + 提案指纹）+ golden 判例（TS 镜像唯一验收标准）。

golden/decomposition.json 双跑：本文件（py 权威）与 web decomposition.test.ts（ts 镜像）消费同一
判例集，逐字节对照（纪律 8）。
"""

import json
from pathlib import Path

from coagentia_contracts.kernel.decomposition import (
    parse_control,
    proposal_fingerprint,
    validate_proposal,
)

GOLDEN = Path(__file__).parents[2] / "fixtures" / "golden" / "decomposition.json"

# 最小合法 decompose 提案（边界测试基底）
_BASE = {
    "version": "coagentia.decomposition.v1",
    "source": "T_SRC",
    "mode": "decompose",
    "summary": "拆两个节点",
    "nodes": [
        {"temp_id": "N1", "title": "甲", "kind": "agent",
         "task_plan": {"goal": "做甲", "acceptance_criteria": [
             {"id": "AC1", "statement": "x", "verify_by": "manual", "verify_ref": ""}]}},
        {"temp_id": "N2", "title": "乙", "kind": "agent",
         "task_plan": {"goal": "做乙", "acceptance_criteria": [
             {"id": "AC1", "statement": "y", "verify_by": "inspect", "verify_ref": ""}]}},
    ],
    "edges": [{"from": "N1", "to": "N2"}],
}
_ENV = {"node_limit": 12, "member_ids": ["M1", "M2"], "bound_project_ids": ["P1"]}


def _codes(errors: list[dict]) -> set[str]:
    return {e["code"] for e in errors}


# ------------------------------------------------------------------ golden 双跑


def test_golden_cases() -> None:
    """golden 判例 = TS 镜像实现的唯一验收标准（纪律 8）。"""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(cases) >= 30
    seen_fns: set[str] = set()
    for case in cases:
        fn = case["fn"]
        seen_fns.add(fn)
        if fn == "validate_proposal":
            got = validate_proposal(case["body"], case["env"])
            assert got == case["errors"], case["name"]
        elif fn == "parse_control":
            body, err = parse_control(case["text"])
            assert (err is None) == case["ok"], case["name"]
            if case["ok"]:
                assert body == case["body"], case["name"]
            else:
                assert err == case["error"], case["name"]
        elif fn == "proposal_fingerprint":
            assert proposal_fingerprint(case["body"]) == case["hash"], case["name"]
        else:
            raise AssertionError(f"未知判例函数: {fn}")
    assert seen_fns == {"validate_proposal", "parse_control", "proposal_fingerprint"}


def test_golden_rule_coverage() -> None:
    """V2–V14 每条规则在 golden 中至少一红一绿（红=出现该码，绿=综合绿全过）。"""
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    red_codes: set[str] = set()
    for case in cases:
        if case["fn"] == "validate_proposal":
            red_codes |= _codes(case["errors"])
    expected = {
        "BAD_VERSION", "UNKNOWN_FIELD", "FIELD_INVALID", "NODE_COUNT", "DUP_ID", "DUP_TITLE",
        "EDGE_UNKNOWN_NODE", "EDGE_SELF", "GRAPH_CYCLE", "PLAN_MISSING", "AC_INVALID",
        "OWNER_NOT_MEMBER", "PROJECT_REQUIRED", "PROJECT_UNBOUND", "MERGE_PLAN_MISSING",
        "SYSTEM_NODE_INVALID",
    }
    assert expected <= red_codes, expected - red_codes
    # 综合绿存在
    greens = [c for c in cases if c["fn"] == "validate_proposal" and not c["errors"]]
    assert any(c["name"] == "green_decompose_full" for c in greens)


# ------------------------------------------------------------------ parse_control 边界


def test_parse_control_happy() -> None:
    body, err = parse_control('正文\n<control>{"a":1}</control>')
    assert err is None
    assert body == {"a": 1}


def test_parse_control_fence_tolerance() -> None:
    for fence in ("```", "```json", "~~~"):
        text = f"说明\n{fence}\n<control>{{\"a\":1}}</control>\n```"
        body, err = parse_control(text)
        assert err is None, fence
        assert body == {"a": 1}


def test_parse_control_missing_multiple_badjson_notobject() -> None:
    assert parse_control("无控制块")[1]["code"] == "CONTROL_PARSE"
    # 多块（含围栏内外重复）
    dup = parse_control("<control>{}</control><control>{}</control>")[1]
    assert dup is not None and "2" in dup["message"]
    # JSON 坏
    assert parse_control("<control>{坏}</control>")[1]["code"] == "CONTROL_PARSE"
    # 非对象
    assert parse_control("<control>[1]</control>")[1]["message"].endswith("必须是 JSON 对象")


def test_parse_control_unclosed_tag_is_missing() -> None:
    body, err = parse_control("<control>{\"a\":1}")  # 无闭合
    assert body is None and err is not None


# ------------------------------------------------------------------ validate 边界补充


def test_validate_happy_empty() -> None:
    assert validate_proposal(_BASE, _ENV) == []


def test_validate_non_dict_body() -> None:
    errs = validate_proposal([], _ENV)
    assert errs == [{"code": "FIELD_INVALID", "path": "$", "message": "提案必须为 JSON 对象"}]


def test_validate_full_collection_multiple_errors() -> None:
    """全量收集：一次跑出多条错误，不遇错即停。"""
    bad = {"version": "x", "mode": "decompose", "nodes": []}  # 缺 source/summary、版本错、nodes 空
    errs = validate_proposal(bad, _ENV)
    codes = _codes(errs)
    assert "BAD_VERSION" in codes
    assert "FIELD_INVALID" in codes  # source/summary 缺
    assert "NODE_COUNT" in codes  # decompose 需 2..12（0 个）
    assert len(errs) >= 4


def test_validate_node_limit_from_env() -> None:
    body = {**_BASE, "nodes": [
        {"temp_id": f"N{i}", "title": f"t{i}", "kind": "agent",
         "task_plan": {"goal": "g", "acceptance_criteria": [
             {"id": "AC1", "statement": "s", "verify_by": "manual", "verify_ref": ""}]}}
        for i in range(1, 4)
    ], "edges": []}
    # node_limit=2 → 3 节点超限
    assert "NODE_COUNT" in _codes(validate_proposal(body, {**_ENV, "node_limit": 2}))
    # node_limit=3 → 恰好通过
    assert validate_proposal(body, {**_ENV, "node_limit": 3}) == []


def test_validate_single_task_clean() -> None:
    single = {
        "version": "coagentia.decomposition.v1", "source": "T", "mode": "single_task",
        "summary": "单任务",
        "nodes": [{"temp_id": "N1", "title": "唯一", "kind": "agent",
                   "task_plan": {"goal": "g", "acceptance_criteria": [
                       {"id": "AC1", "statement": "s", "verify_by": "command",
                        "verify_ref": "pytest"}]}}],
    }
    assert validate_proposal(single, _ENV) == []


def test_validate_writes_code_needs_project_and_merge_plan() -> None:
    body = json.loads(json.dumps(_BASE))
    body["nodes"][0]["writes_code"] = True  # 无 project、无 merge_plan
    codes = _codes(validate_proposal(body, _ENV))
    assert "PROJECT_REQUIRED" in codes
    assert "MERGE_PLAN_MISSING" in codes


def test_validate_ref_semantics_exact_id_match() -> None:
    """env ref 语义：suggested_owner/project 一律 id 精确匹配（非名字解析）。"""
    body = json.loads(json.dumps(_BASE))
    body["nodes"][0]["suggested_owner"] = "M1"  # 精确 id 命中
    assert validate_proposal(body, _ENV) == []
    body["nodes"][0]["suggested_owner"] = "m1"  # 大小写不同 → 不命中
    assert "OWNER_NOT_MEMBER" in _codes(validate_proposal(body, _ENV))


def test_validate_kernel_ge_taskplanbody_strictness() -> None:
    """不变量：kernel 校验 ≥ TaskPlanBody 消费严格度（AC 四字段全必填、空串不禁）。"""
    # AC 缺 id / verify_ref（statement/verify_by 在）→ 两条 AC_INVALID 且 path 精确到字段
    body = json.loads(json.dumps(_BASE))
    body["nodes"][0]["task_plan"]["acceptance_criteria"] = [
        {"statement": "s", "verify_by": "manual"}]
    errs = validate_proposal(body, _ENV)
    paths = {e["path"] for e in errs if e["code"] == "AC_INVALID"}
    assert "$.nodes[0].task_plan.acceptance_criteria[0].id" in paths
    assert "$.nodes[0].task_plan.acceptance_criteria[0].verify_ref" in paths
    # 空串不禁（TaskPlanBody 不禁，未列出的不发明）——id/statement/verify_ref 空串合法
    body2 = json.loads(json.dumps(_BASE))
    body2["nodes"][0]["task_plan"]["acceptance_criteria"] = [
        {"id": "", "statement": "", "verify_by": "manual", "verify_ref": ""}]
    assert validate_proposal(body2, _ENV) == []


def test_validate_unknown_fields_all_levels() -> None:
    """V3 未知字段执法到全部层级：task_plan / AC / edge（深层无别名 hint）。"""
    body = json.loads(json.dumps(_BASE))
    body["nodes"][0]["task_plan"]["estimate"] = 5
    body["nodes"][0]["task_plan"]["acceptance_criteria"][0]["note"] = "x"
    body["edges"][0]["label"] = "依赖"
    errs = validate_proposal(body, _ENV)
    unknown = {e["path"]: e for e in errs if e["code"] == "UNKNOWN_FIELD"}
    assert set(unknown) == {
        "$.nodes[0].task_plan.estimate",
        "$.nodes[0].task_plan.acceptance_criteria[0].note",
        "$.edges[0].label",
    }
    assert all("hint" not in e for e in unknown.values())  # 深层无别名清单


def test_validate_task_plan_version_and_arrays() -> None:
    """task_plan.version 错值红/缺席合法；defaults_decided/out_of_scope 须字符串数组。"""
    # version 错值 → FIELD_INVALID（TaskPlanBody Literal 带默认：缺席合法、错值落地必炸）
    body = json.loads(json.dumps(_BASE))
    body["nodes"][0]["task_plan"]["version"] = "coagentia.task-plan.v2"
    errs = validate_proposal(body, _ENV)
    assert any(
        e["code"] == "FIELD_INVALID" and e["path"] == "$.nodes[0].task_plan.version"
        for e in errs
    )
    # version 正确 + 数组字段合法 → 绿
    body2 = json.loads(json.dumps(_BASE))
    body2["nodes"][0]["task_plan"]["version"] = "coagentia.task-plan.v1"
    body2["nodes"][0]["task_plan"]["defaults_decided"] = ["用 SQLite"]
    body2["nodes"][0]["task_plan"]["out_of_scope"] = []
    assert validate_proposal(body2, _ENV) == []
    # 非数组 / 元素非字符串 → FIELD_INVALID（path 到字段与元素）
    body3 = json.loads(json.dumps(_BASE))
    body3["nodes"][0]["task_plan"]["defaults_decided"] = "x"
    body3["nodes"][0]["task_plan"]["out_of_scope"] = ["ok", 1]
    errs3 = validate_proposal(body3, _ENV)
    paths3 = {e["path"] for e in errs3 if e["code"] == "FIELD_INVALID"}
    assert "$.nodes[0].task_plan.defaults_decided" in paths3
    assert "$.nodes[0].task_plan.out_of_scope[1]" in paths3


def test_validate_length_semantics_codepoints() -> None:
    """长度语义 = Unicode 码点：增补平面字符按 1 字计（py len 即码点；TS 镜像须对齐）。"""
    body = json.loads(json.dumps(_BASE))
    body["nodes"][0]["title"] = "甲" * 79 + "🍅"  # 80 码点（UTF-16 码元 81）→ 绿
    assert validate_proposal(body, _ENV) == []
    body["nodes"][0]["title"] = "甲" * 80 + "🍅"  # 81 码点 → 红
    assert "FIELD_INVALID" in _codes(validate_proposal(body, _ENV))


# ------------------------------------------------------------------ 指纹边界


def test_fingerprint_strips_system_fields() -> None:
    clean = proposal_fingerprint(_BASE)
    injected = json.loads(json.dumps(_BASE))
    injected["revision"] = 5
    injected["proposed_by"] = "M1"
    injected["proposed_at"] = "2026-07-12T00:00:00.000Z"
    assert proposal_fingerprint(injected) == clean


def test_fingerprint_order_invariant() -> None:
    shuffled = json.loads(json.dumps(_BASE))
    shuffled["nodes"] = [shuffled["nodes"][1], shuffled["nodes"][0]]
    assert proposal_fingerprint(shuffled) == proposal_fingerprint(_BASE)


def test_fingerprint_is_sha256_hex() -> None:
    h = proposal_fingerprint(_BASE)
    assert len(h) == 64 and h == h.lower()
