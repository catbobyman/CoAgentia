/* eslint-disable */
/**
 * 生成物，禁止手改（pnpm gen 重新生成）。
 * 源 = packages/contracts 的 Pydantic 模型（契约 A–E 的唯一源）。
 */
/** daemon 协议/缓冲/工具白名单运行时常量
 *  （源 = packages/contracts 的 daemon.py 与 constants.py，经 build/constants.json）。 */
export const ACK_TIMEOUT_SEC = 10;
export const BUFFER_DEPLOY_LOG_MAX_BYTES = 5242880;
export const BUFFER_DIAGNOSTICS_MAX = 10000;
export const BUFFER_USAGE_MAX = 100000;
export const CLOSE_PROTOCOL_MISMATCH = 4400;
export const CLOSE_SUPERSEDED = 4001;
export const COAGENTIA_MCP_TOOLS = [
  "send_message",
  "get_messages",
  "get_thread",
  "upload_file",
  "get_file",
  "create_reminder",
  "cancel_reminder",
  "list_channels",
  "list_members",
  "list_tasks",
  "get_task",
  "claim_task",
  "unclaim_task",
  "set_task_status",
  "search",
  "trigger_deploy",
  "submit_task_contract",
  "create_task",
  "trigger_merge"
];
export const CODEX_DISALLOWED_TOOLS = [];
export const DAEMON_PROTOCOL_V = 1;
export const DAEMON_WS_PATH = "/api/daemon/ws";
export const DISALLOWED_TOOLS = [
  "EnterPlanMode",
  "ExitPlanMode",
  "SendMessage",
  "CronCreate",
  "CronDelete",
  "CronList",
  "ScheduleWakeup",
  "TaskCreate",
  "TaskGet",
  "TaskList",
  "TaskOutput",
  "TaskStop",
  "TaskUpdate",
  "Workflow",
  "EnterWorktree",
  "ExitWorktree",
  "DesignSync"
];
export const RECONCILE_INTERVAL_SEC = 60;
