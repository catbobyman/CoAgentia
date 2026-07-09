// TS 生成（生成管线第二步）：build/*.json → src/generated/*.ts
// 生成物提交入仓、禁止手改；重跑后 git diff 为空 = 两侧同步（00 §4.4 第一道闸）。
import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { compileFromFile } from "json-schema-to-typescript";

const here = dirname(fileURLToPath(import.meta.url));
const repo = join(here, "..", "..");
const out = join(here, "src", "generated");
mkdirSync(out, { recursive: true });

const BANNER = `/* eslint-disable */
/**
 * 生成物，禁止手改（pnpm gen 重新生成）。
 * 源 = packages/contracts 的 Pydantic 模型（契约 A–E 的唯一源）。
 */
`;

// 1) 实体/WS/daemon 模型 ← contracts.schema.json
const models = await compileFromFile(join(repo, "build", "contracts.schema.json"), {
  additionalProperties: false,
  bannerComment: BANNER,
  style: { singleQuote: true },
});
writeFileSync(join(out, "models.ts"), models);

// 2) REST 路径/操作类型 ← openapi.json（mock server 导出，路由 = 契约 B M1 端点）
execSync(
  `pnpm exec openapi-typescript "${join(repo, "build", "openapi.json")}" -o "${join(out, "rest.ts")}"`,
  { stdio: "inherit", cwd: here },
);

console.log("generated: src/generated/models.ts, src/generated/rest.ts");
