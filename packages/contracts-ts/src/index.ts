// @coagentia/contracts-ts 入口：仅转发生成物（本包无手写形状）。
export type * from './generated/models';
export type { paths as RestPaths } from './generated/rest';
// 运行时常量（值导出，非 type）：状态机边表 + claim 语义门，纪律 7 单一事实源。
export { TASK_TRANSITIONS, UNCLAIMABLE_STATUSES } from './generated/constants';
