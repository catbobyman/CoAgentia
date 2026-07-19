/**
 * 数据目录布局（契约 D §9.1/§9.3）：~/.coagentia/ 下 daemon/、agents/ 与 worktrees/。
 *
 * - 支持测试注入临时根目录（root 参数）；
 * - daemon/buffer/（离线遥测缓冲）、daemon/state/<member_id>.json（会话簿记位，A7 用）、
 *   daemon.log（daemon 自身进程日志，≠ 诊断事件）；
 * - agents/<member_id>/（Agent Home，daemon 只在创建时建目录、reset_full 清空、查询帧只读遍历）。
 *
 * 对等基准 = apps/daemon paths.py。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

export type JsonObject = Record<string, unknown>;

export class DataPaths {
  readonly root: string;

  constructor(root?: string) {
    this.root = root ?? path.join(os.homedir(), '.coagentia');
  }

  // ---- 目录 ----
  get daemonDir(): string {
    return path.join(this.root, 'daemon');
  }

  get bufferDir(): string {
    return path.join(this.daemonDir, 'buffer');
  }

  get stateDir(): string {
    return path.join(this.daemonDir, 'state');
  }

  get agentsDir(): string {
    return path.join(this.root, 'agents');
  }

  get worktreesDir(): string {
    return path.join(this.root, 'worktrees');
  }

  get logPath(): string {
    return path.join(this.daemonDir, 'daemon.log');
  }

  ensureDirs(): void {
    for (const d of [this.daemonDir, this.bufferDir, this.stateDir, this.agentsDir, this.worktreesDir]) {
      fs.mkdirSync(d, { recursive: true });
    }
  }

  // ---- Project worktree（契约 D §9.1：project_id/task_id 固定短组件）----
  worktreePath(projectId: string, taskId: string): string {
    return path.join(this.worktreesDir, projectId, taskId);
  }

  // ---- Agent Home（契约 D §9.3：member_id 命名，非名字）----
  agentHome(memberId: string): string {
    return path.join(this.agentsDir, memberId);
  }

  ensureAgentHome(memberId: string): string {
    const home = this.agentHome(memberId);
    fs.mkdirSync(home, { recursive: true });
    return home;
  }

  /** reset_full：清空 Home 目录内容，目录本身保留（契约 D §5.1/§9.3）。 */
  clearAgentHome(memberId: string): void {
    const home = this.agentHome(memberId);
    if (!fs.existsSync(home)) {
      fs.mkdirSync(home, { recursive: true });
      return;
    }
    for (const child of fs.readdirSync(home)) {
      fs.rmSync(path.join(home, child), { recursive: true, force: true });
    }
  }

  // ---- 会话簿记（daemon/state/<member_id>.json，契约 D §9.1；A7 --resume 用）----
  sessionFile(memberId: string): string {
    return path.join(this.stateDir, `${memberId}.json`);
  }

  readSession(memberId: string): JsonObject {
    const f = this.sessionFile(memberId);
    if (!fs.existsSync(f)) return {};
    try {
      const parsed: unknown = JSON.parse(fs.readFileSync(f, 'utf-8'));
      return typeof parsed === 'object' && parsed !== null ? (parsed as JsonObject) : {};
    } catch {
      return {};
    }
  }

  writeSession(memberId: string, data: JsonObject): void {
    fs.mkdirSync(this.stateDir, { recursive: true });
    fs.writeFileSync(this.sessionFile(memberId), JSON.stringify(data), 'utf-8');
  }

  clearSession(memberId: string): void {
    const f = this.sessionFile(memberId);
    if (fs.existsSync(f)) fs.unlinkSync(f);
  }
}
