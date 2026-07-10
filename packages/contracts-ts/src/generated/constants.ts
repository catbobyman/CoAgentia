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
