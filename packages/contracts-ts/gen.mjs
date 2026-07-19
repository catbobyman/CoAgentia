// TS 生成（生成管线第二步）：build/*.json → src/generated/*.ts
// 生成物提交入仓、禁止手改；重跑后 git diff 为空 = 两侧同步（00 §4.4 第一道闸）。
import { execSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
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

// 3) 运行时常量（状态机边表）← constants.json（纪律 7：TS 侧防呆与 server 校验同源，非手写字面量）
const constants = JSON.parse(readFileSync(join(repo, "build", "constants.json"), "utf8"));
const constTs =
  BANNER +
  `import type { TaskStatus } from './models';\n\n` +
  `/** 任务状态机合法边（源 = packages/contracts constants.py TASK_TRANSITIONS）。\n` +
  ` *  值 = 合法目标态数组（不含自身；空数组 = 终态）。前端拖列禁用/按钮置灰消费此表（纪律 7）。 */\n` +
  `export const TASK_TRANSITIONS: Record<TaskStatus, TaskStatus[]> = ${JSON.stringify(
    constants.TASK_TRANSITIONS,
    null,
    2,
  )};\n\n` +
  `/** claim 语义门：终态不可认领（源 = constants.py UNCLAIMABLE_STATUSES）。前端认领钮防呆消费。 */\n` +
  `export const UNCLAIMABLE_STATUSES: TaskStatus[] = ${JSON.stringify(
    constants.UNCLAIMABLE_STATUSES,
    null,
    2,
  )};\n\n` +
  `/** Orchestrator 内置角色模板展示常量（源 = constants.py ORCHESTRATOR_ROLE_TEMPLATE_*）。\n` +
  ` *  创建 Agent 弹窗角色模板段预填 + NO_ORCHESTRATOR 引导链的 MVP 唯一数据源（纪律 7）。 */\n` +
  `export const ORCHESTRATOR_ROLE_TEMPLATE_KEY = ${JSON.stringify(
    constants.ORCHESTRATOR_ROLE_TEMPLATE_KEY,
  )};\n` +
  `export const ORCHESTRATOR_ROLE_TEMPLATE_NAME = ${JSON.stringify(
    constants.ORCHESTRATOR_ROLE_TEMPLATE_NAME,
  )};\n` +
  `export const ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL = ${JSON.stringify(
    constants.ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL,
  )};\n`;
writeFileSync(join(out, "constants.ts"), constTs);

// 4) daemon-ts 运行时常量（TS 迁移批裁决 #6）：daemon-ts 对本包只许 import type（node 直跑
//    无法运行时解析本包的无扩展名导入），运行时值由此处单独产出到 daemon-ts 包内。
const daemonOut = join(repo, "apps", "daemon-ts", "src", "generated");
mkdirSync(daemonOut, { recursive: true });
const d = constants.DAEMON;
const lit = (v) => JSON.stringify(v, null, v && typeof v === "object" ? 2 : undefined);
const daemonConstTs =
  BANNER +
  `/** daemon 协议/缓冲/工具白名单运行时常量\n` +
  ` *  （源 = packages/contracts 的 daemon.py 与 constants.py，经 build/constants.json）。 */\n` +
  Object.entries(d)
    .map(([k, v]) => `export const ${k} = ${lit(v)};\n`)
    .join("");
writeFileSync(join(daemonOut, "constants.ts"), daemonConstTs);

console.log(
  "generated: src/generated/{models,rest,constants}.ts + apps/daemon-ts/src/generated/constants.ts",
);
