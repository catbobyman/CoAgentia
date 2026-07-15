"""导出契约 schema（生成管线第一步，00 §4.4 单向生成）：

1. build/contracts.schema.json —— 全部 Pydantic 模型的 JSON Schema 汇总
   （实体/WS/daemon/REST 请求响应 → json-schema-to-typescript 的输入）；
2. build/openapi.json —— mock server 的 OpenAPI 导出
   （路由 = 契约 B 的 M1 端点，response_model = contracts 模型 → openapi-typescript 的输入）。

输出确定性排序，提交入仓后 `git diff` 为空 = 生成物与源同步（守门检查）。
运行：uv run python scripts/export_schemas.py
"""

import inspect
import json
from pathlib import Path

from coagentia_contracts import daemon, entities, rest, ws
from coagentia_contracts.entities import ContractModel
from pydantic.json_schema import models_json_schema

BUILD = Path(__file__).parents[1] / "build"


def collect_models() -> list[type[ContractModel]]:
    models: dict[str, type[ContractModel]] = {}
    for module in (entities, ws, daemon, rest):
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (issubclass(obj, ContractModel) and obj is not ContractModel
                    and obj.__module__ == module.__name__):
                models[obj.__name__] = obj
    return [models[k] for k in sorted(models)]


def main() -> None:
    BUILD.mkdir(exist_ok=True)
    models = collect_models()
    _, top = models_json_schema(
        [(m, "validation") for m in models], ref_template="#/$defs/{model}"
    )
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "CoAgentiaContracts",
        "type": "object",
        "properties": {m.__name__: {"$ref": f"#/$defs/{m.__name__}"} for m in models},
        "$defs": dict(sorted(top["$defs"].items())),
    }
    (BUILD / "contracts.schema.json").write_text(
        json.dumps(schema, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    from coagentia_mock.app import app

    (BUILD / "openapi.json").write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # 运行时常量：状态机边表 + claim 语义门 + Orchestrator 角色模板展示常量 → TS 防呆/展示与
    # server 同源（纪律 7 单一事实源）。
    from coagentia_contracts.constants import (
        ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
        ORCHESTRATOR_ROLE_TEMPLATE_KEY,
        ORCHESTRATOR_ROLE_TEMPLATE_NAME,
        TASK_TRANSITIONS,
        UNCLAIMABLE_STATUSES,
    )

    transitions = {
        frm.value: sorted(to.value for to in tos) for frm, tos in TASK_TRANSITIONS.items()
    }
    (BUILD / "constants.json").write_text(
        json.dumps(
            {
                "ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL": (
                    ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL
                ),
                "ORCHESTRATOR_ROLE_TEMPLATE_KEY": ORCHESTRATOR_ROLE_TEMPLATE_KEY,
                "ORCHESTRATOR_ROLE_TEMPLATE_NAME": ORCHESTRATOR_ROLE_TEMPLATE_NAME,
                "TASK_TRANSITIONS": dict(sorted(transitions.items())),
                "UNCLAIMABLE_STATUSES": sorted(s.value for s in UNCLAIMABLE_STATUSES),
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"{len(models)} models -> build/contracts.schema.json; "
        "openapi -> build/openapi.json; constants -> build/constants.json"
    )


if __name__ == "__main__":
    main()
