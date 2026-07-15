"""生成指纹金标判例（packages/fixtures/golden/fingerprint.json）。

判例的 canonical 串可人工目检（规范 = 契约 A §2）；sha256 由 Python 权威实现计算。
TS 镜像实现（M6）必须逐判例一致。运行：uv run python scripts/gen_golden_fingerprint.py
"""

import json
from pathlib import Path

from coagentia_contracts.kernel import canonicalize, fingerprint

CASES: list[tuple[str, object]] = [
    ("empty_object", {}),
    ("empty_snapshot", {"edges": [], "nodes": []}),
    ("key_sorting", {"b": 1, "a": 2}),
    ("codepoint_order_upper_before_lower", {"a": 2, "Z": 1}),
    ("null_pruning_recursive", {"a": None, "b": {"c": None, "d": 1}}),
    ("unicode_unescaped", {"名": "值", "emoji": "🍅"}),
    ("array_order_preserved", {"a": [3, 1, 2], "b": True, "f": False}),
    ("nested_canvas_like", {
        "nodes": [
            {"id": "N1", "kind": "agent", "task_id": "01JZKJ7GG0000000000000000T",
             "is_summary": False, "system_action": None, "command": None},
            {"id": "N2", "kind": "system", "task_id": None,
             "is_summary": False, "system_action": "check", "command": "pnpm test"},
        ],
        "edges": [{"from": "N1", "to": "N2"}],
    }),
    ("integers_only", {"count": 42, "zero": 0, "neg": -7}),
    ("string_escapes", {"quote": 'say "hi"', "backslash": "a\\b", "newline": "l1\nl2"}),
]


def main() -> None:
    out = [
        {
            "name": name,
            "input": value,
            "canonical": canonicalize(value),  # type: ignore[arg-type]
            "sha256": fingerprint(value),  # type: ignore[arg-type]
        }
        for name, value in CASES
    ]
    path = Path(__file__).parents[1] / "packages" / "fixtures" / "golden" / "fingerprint.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {len(out)} cases -> {path}")


if __name__ == "__main__":
    main()
