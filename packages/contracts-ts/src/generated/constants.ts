/* eslint-disable */
/**
 * 生成物，禁止手改（pnpm gen 重新生成）。
 * 源 = packages/contracts 的 Pydantic 模型（契约 A–E 的唯一源）。
 */
import type { TaskStatus } from './models';

/** 任务状态机合法边（源 = packages/contracts constants.py TASK_TRANSITIONS）。
 *  值 = 合法目标态数组（不含自身；空数组 = 终态）。前端拖列禁用/按钮置灰消费此表（纪律 7）。 */
export const TASK_TRANSITIONS: Record<TaskStatus, TaskStatus[]> = {
  "closed": [
    "todo"
  ],
  "done": [],
  "in_progress": [
    "closed",
    "in_review",
    "todo"
  ],
  "in_review": [
    "closed",
    "done",
    "in_progress"
  ],
  "todo": [
    "closed",
    "in_progress"
  ]
};

/** claim 语义门：终态不可认领（源 = constants.py UNCLAIMABLE_STATUSES）。前端认领钮防呆消费。 */
export const UNCLAIMABLE_STATUSES: TaskStatus[] = [
  "closed",
  "done"
];

/** Orchestrator 内置角色模板展示常量（源 = constants.py ORCHESTRATOR_ROLE_TEMPLATE_*）。
 *  创建 Agent 弹窗角色模板段预填 + NO_ORCHESTRATOR 引导链的 MVP 唯一数据源（纪律 7）。 */
export const ORCHESTRATOR_ROLE_TEMPLATE_KEY = "orchestrator";
export const ORCHESTRATOR_ROLE_TEMPLATE_NAME = "Orchestrator（对话式委派协调者）";
export const ORCHESTRATOR_ROLE_TEMPLATE_DESCRIPTION_PREFILL = "本频道的对话式委派协调者：@它并给一句话需求，它会理解澄清后用 create_task 逐个派活（正文 @建议负责人即唤醒，认领仍走 claim 防重）、盯交付进展、经 trigger_merge 指挥合并入主干，并阶段性汇总进展与风险（判断归模型、控制归引擎——DEDAG 委派模式）。";
