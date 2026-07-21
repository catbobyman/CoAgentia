# P0 TypeScript 生成器输入快照

本目录仅用于在无 Python、无 Git 的 fresh Windows CI 中重放当前 `pnpm gen:ts`，证明已提交生成物与连续两次生成结果字节一致。

它是 P0 环境校准快照，不是产品契约权威，也不替代 P1 对 schema/OpenAPI 的正式冻结。权威仍是根 `plan.md` 与 `engineering_docs/` 契约；快照由 `pnpm gen:schemas` 从既有 Python 实现导出。

采集于提交 `82f6f82b26d3440bb05981f905d7d4a9ca81d338` 的 clean 产品树：

- `contracts.schema.json`: `55357c3b1131c589db3143aafa6e6af7f6923c5c1e2dba841b9a3d3a521cff55`
- `openapi.json`: `5dbdf05f25e9b17f220164f744ebd03e52fa970ad86b29590b6dd423342c2969`
- `constants.json`: `21011e41be5cd38ce00756d262e507dd63110e7789a2bb560f128f68aba69d5a`
