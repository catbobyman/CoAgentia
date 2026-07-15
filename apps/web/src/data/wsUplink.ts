// M7b WS 上行订阅管理（deploy_log 流，订阅制）。契约 C：deployment.log 只发订阅该 deployment 的连接，
// 上行帧 SubDeployLogMsg{type:sub|unsub, stream:"deploy_log", deployment_id}（ws/hub.py 落订阅集）。
// server 侧订阅集是 per-connection 内存态、重连即失，故前端在此维护活跃订阅集，在断线重连成功
// （sys.hello）后整体重发（resubscribe），避免重连后静默丢失实时日志。ws.ts 在 socket open/close 时
// 设置/清空 sender、在重连 hello 后调 resendSubscriptions；DeploymentCard 挂载/卸载时 sub/unsub。
type Sender = (msg: unknown) => void;

let sender: Sender | null = null;
// 活跃订阅：key = `deploy_log:${deployment_id}` → 订阅上行帧（重连重发的载荷）。
const active = new Map<string, unknown>();

/** ws.ts 注入实际下发通道（socket OPEN 时的 send；断开时置 null，订阅只登记不下发）。 */
export function setWsSender(next: Sender | null): void {
  sender = next;
}

/** 断线重连成功（sys.hello）后重发全部活跃订阅：server 重连丢订阅集，前端负责恢复。 */
export function resendSubscriptions(): void {
  if (!sender) return;
  for (const msg of active.values()) sender(msg);
}

function subKey(deploymentId: string): string {
  return `deploy_log:${deploymentId}`;
}

/** 订阅 deploy_log 流：登记（供重连重发）+ 立即下发（连接可用时）。同 deployment 重复订阅幂等。 */
export function subscribeDeployLog(deploymentId: string): void {
  const msg = { type: 'sub', stream: 'deploy_log', deployment_id: deploymentId };
  active.set(subKey(deploymentId), msg);
  sender?.(msg);
}

/** 退订 deploy_log 流：注销（不再重连重发）+ 下发 unsub（连接可用时）。 */
export function unsubscribeDeployLog(deploymentId: string): void {
  active.delete(subKey(deploymentId));
  sender?.({ type: 'unsub', stream: 'deploy_log', deployment_id: deploymentId });
}

/** 测试辅助：当前活跃订阅键集（只读快照）。 */
export function activeSubscriptionKeys(): string[] {
  return [...active.keys()];
}
