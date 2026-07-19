/**
 * 遥测缓冲（契约 D §7 缓冲纪律 / §9.1 daemon/buffer/）：需 ack 类上报的离线落盘 + 重传。
 * 对等基准 = apps/daemon buffer.py（零行为改进）。
 *
 * 五条独立缓冲：
 * - diagnostics.jsonl：重复可容忍（铁律 5），无客户端主键，ack 后按已发条数移除；
 * - usage.jsonl：以适配器 ULID 主键，exactly-once 去重根基；ack 后按 id 集合移除；
 * - check-finished.jsonl：以 run_id 自然键去重；server 落终态并 ack 后移除；
 * - deploy-log.jsonl：以 (deployment_id, chunk_seq) 去重；
 * - deploy-finished.jsonl：以 deployment_id 去重（已终态重发经 find 重报）。
 *
 * 环形上限（constants.BUFFER_*）：溢出丢最旧并追加一条 daemon.buffer_overflow 诊断（丢弃计数可见）。
 * 落盘跨 daemon 重启：每次变更以同目录临时文件**整文件原子重写**（temp 写入 + fsyncSync +
 * renameSync；win32 rename 可覆盖既有文件，对等 py mkstemp→fsync→os.replace）。
 * 重传**不虚增**——ULID 落盘后不再生成。O(n) 全量重写策略维持 py 原样（勿改追加制）。
 */

import { randomBytes } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

import type { BufferedCounts, CheckFinishedData, DeployFinishedData, DeployLogReportData, DiagnosticEventIn, TokenUsageEventIn } from '@coagentia/contracts-ts';

import { BUFFER_DIAGNOSTICS_MAX, BUFFER_USAGE_MAX } from './generated/constants.ts';
import type { DataPaths, JsonObject } from './paths.ts';
import { nowIso } from './util.ts';

const OVERFLOW_TYPE = 'daemon.buffer_overflow';

/**
 * 测试可替换的落盘操作接缝（对等 py 测试 monkeypatch `buffer_module.json.dumps` /
 * `buffer_module.os.fsync` / `buffer_module.os.replace`——node:fs 命名空间对象冻结不可
 * spy，故以本对象为唯一补丁点；生产路径行为与直接调用 fs 完全一致）。
 */
export const _io = {
  dumps: (row: JsonObject): string => JSON.stringify(row),
  fsync: (fd: number): void => {
    fs.fsyncSync(fd);
  },
  replace: (source: string, target: string): void => {
    fs.renameSync(source, target);
  },
};

/** diagnostics / usage / check.finished（+ deploy.log / deploy.finished）缓冲 JSONL 落盘。 */
export class TelemetryBuffer {
  private readonly _paths: DataPaths;
  private readonly _diagMax: number;
  private readonly _usageMax: number;
  private _diag: JsonObject[] = [];
  private _usage: JsonObject[] = [];
  private _checks: JsonObject[] = [];
  private _deployLogs: JsonObject[] = [];
  private _deployFinished: JsonObject[] = [];
  private _droppedDiag = 0;
  private _droppedUsage = 0;

  constructor(paths: DataPaths, opts: { diagnosticsMax?: number; usageMax?: number } = {}) {
    this._paths = paths;
    this._diagMax = opts.diagnosticsMax ?? BUFFER_DIAGNOSTICS_MAX;
    this._usageMax = opts.usageMax ?? BUFFER_USAGE_MAX;
    this._load();
  }

  // ---------------------------------------------------------------- 落盘装载/重写

  private get _diagPath(): string {
    return path.join(this._paths.bufferDir, 'diagnostics.jsonl');
  }

  private get _usagePath(): string {
    return path.join(this._paths.bufferDir, 'usage.jsonl');
  }

  private get _checkPath(): string {
    return path.join(this._paths.bufferDir, 'check-finished.jsonl');
  }

  private get _deployLogPath(): string {
    return path.join(this._paths.bufferDir, 'deploy-log.jsonl');
  }

  private get _deployFinishedPath(): string {
    return path.join(this._paths.bufferDir, 'deploy-finished.jsonl');
  }

  private _load(): void {
    fs.mkdirSync(this._paths.bufferDir, { recursive: true });
    this._diag = readJsonl(this._diagPath);
    this._usage = readJsonl(this._usagePath);
    this._checks = readJsonl(this._checkPath);
    this._deployLogs = readJsonl(this._deployLogPath);
    this._deployFinished = readJsonl(this._deployFinishedPath);
  }

  private _rewriteDiag(): void {
    writeJsonl(this._diagPath, this._diag);
  }

  private _rewriteUsage(): void {
    writeJsonl(this._usagePath, this._usage);
  }

  private _rewriteChecks(): void {
    writeJsonl(this._checkPath, this._checks);
  }

  private _rewriteDeployLogs(): void {
    writeJsonl(this._deployLogPath, this._deployLogs);
  }

  private _rewriteDeployFinished(): void {
    writeJsonl(this._deployFinishedPath, this._deployFinished);
  }

  // ---------------------------------------------------------------- 追加（含溢出处置）

  appendDiagnostic(event: DiagnosticEventIn): void {
    this._diag.push({ ...event });
    if (this._diag.length > this._diagMax) {
      const overflow = this._diag.length - this._diagMax;
      this._diag.splice(0, overflow);
      this._droppedDiag += overflow;
      this._appendOverflowMarker('diagnostics', this._droppedDiag);
    }
    this._rewriteDiag();
  }

  appendUsage(event: TokenUsageEventIn): void {
    this._usage.push({ ...event });
    if (this._usage.length > this._usageMax) {
      const overflow = this._usage.length - this._usageMax;
      this._usage.splice(0, overflow);
      this._droppedUsage += overflow;
      // usage 溢出计入 diagnostics 缓冲（成本口径尽量不丢，但仍留痕）。
      this._appendOverflowMarker('usage', this._droppedUsage);
    }
    this._rewriteUsage();
  }

  /** check.finished 以 run_id 去重落盘；未 ack 前重启仍可原样重传。 */
  appendCheck(event: CheckFinishedData): void {
    const row: JsonObject = { ...event };
    const index = this._checks.findIndex((current) => current['run_id'] === event.run_id);
    if (index >= 0) {
      this._checks[index] = row;
      this._rewriteChecks();
      return;
    }
    this._checks.push(row);
    this._rewriteChecks();
  }

  /** 溢出留痕：追加一条 daemon.buffer_overflow 诊断（不再触发二次溢出判定）。 */
  private _appendOverflowMarker(bufferName: string, droppedTotal: number): void {
    const marker: JsonObject = {
      type: OVERFLOW_TYPE,
      payload: { buffer: bufferName, dropped_total: droppedTotal },
      at: nowIso(),
    };
    this._diag.push(marker);
    if (this._diag.length > this._diagMax) {
      this._diag.splice(0, this._diag.length - this._diagMax);
    }
  }

  // ---------------------------------------------------------------- 读取/确认（重传语义）

  peekDiagnostics(n: number): DiagnosticEventIn[] {
    return this._diag.slice(0, n).map((e) => ({ ...e })) as unknown as DiagnosticEventIn[];
  }

  peekUsage(n: number): TokenUsageEventIn[] {
    return this._usage.slice(0, n).map((e) => ({ ...e })) as unknown as TokenUsageEventIn[];
  }

  /** 确认前 count 条已落库 → 移除（重复可容忍，按发送顺序移除）。 */
  ackDiagnostics(count: number): void {
    if (count <= 0) return;
    this._diag.splice(0, count);
    this._rewriteDiag();
  }

  /** 确认给定 ULID 已 exactly-once 落库 → 按 id 移除（未 ack 的保留待重传）。 */
  ackUsage(ids: string[]): void {
    if (ids.length === 0) return;
    const drop = new Set<unknown>(ids);
    this._usage = this._usage.filter((e) => !drop.has(e['id']));
    this._rewriteUsage();
  }

  peekChecks(n: number): CheckFinishedData[] {
    return this._checks.slice(0, n).map((e) => ({ ...e })) as unknown as CheckFinishedData[];
  }

  ackChecks(runIds: string[]): void {
    if (runIds.length === 0) return;
    const drop = new Set<unknown>(runIds);
    this._checks = this._checks.filter((e) => !drop.has(e['run_id']));
    this._rewriteChecks();
  }

  findCheck(runId: string): CheckFinishedData | null {
    const row = this._checks.find((e) => e['run_id'] === runId);
    return row !== undefined ? ({ ...row } as unknown as CheckFinishedData) : null;
  }

  // ---------------------------------------------------------------- deploy.log（去重键 =
  // (deployment_id, chunk_seq)；需 ack，server 按已收 max chunk_seq 去重）。

  /** deploy.log 以 (deployment_id, chunk_seq) 去重落盘；未 ack 前重启仍可原样重传。 */
  appendDeployLog(data: DeployLogReportData): void {
    const row: JsonObject = { ...data };
    const index = this._deployLogs.findIndex(
      (current) =>
        current['deployment_id'] === data.deployment_id && current['chunk_seq'] === data.chunk_seq,
    );
    if (index >= 0) {
      this._deployLogs[index] = row;
      this._rewriteDeployLogs();
      return;
    }
    this._deployLogs.push(row);
    this._rewriteDeployLogs();
  }

  peekDeployLogs(n: number): DeployLogReportData[] {
    return this._deployLogs.slice(0, n).map((e) => ({ ...e })) as unknown as DeployLogReportData[];
  }

  ackDeployLog(deploymentId: string, chunkSeq: number): void {
    this._deployLogs = this._deployLogs.filter(
      (e) => !(e['deployment_id'] === deploymentId && e['chunk_seq'] === chunkSeq),
    );
    this._rewriteDeployLogs();
  }

  hasDeployLogs(): boolean {
    return this._deployLogs.length > 0;
  }

  // ---------------------------------------------------------------- deploy.finished（去重键 =
  // deployment_id；需 ack，已终态重发经 find 重报）。

  appendDeployFinished(data: DeployFinishedData): void {
    const row: JsonObject = { ...data };
    const index = this._deployFinished.findIndex(
      (current) => current['deployment_id'] === data.deployment_id,
    );
    if (index >= 0) {
      this._deployFinished[index] = row;
      this._rewriteDeployFinished();
      return;
    }
    this._deployFinished.push(row);
    this._rewriteDeployFinished();
  }

  peekDeployFinished(n: number): DeployFinishedData[] {
    return this._deployFinished
      .slice(0, n)
      .map((e) => ({ ...e })) as unknown as DeployFinishedData[];
  }

  ackDeployFinished(deploymentIds: string[]): void {
    if (deploymentIds.length === 0) return;
    const drop = new Set<unknown>(deploymentIds);
    this._deployFinished = this._deployFinished.filter((e) => !drop.has(e['deployment_id']));
    this._rewriteDeployFinished();
  }

  hasDeployFinished(): boolean {
    return this._deployFinished.length > 0;
  }

  findDeployFinished(deploymentId: string): DeployFinishedData | null {
    const row = this._deployFinished.find((e) => e['deployment_id'] === deploymentId);
    return row !== undefined ? ({ ...row } as unknown as DeployFinishedData) : null;
  }

  counts(): BufferedCounts {
    return { diagnostics: this._diag.length, usage: this._usage.length };
  }

  hasDiagnostics(): boolean {
    return this._diag.length > 0;
  }

  hasUsage(): boolean {
    return this._usage.length > 0;
  }

  hasChecks(): boolean {
    return this._checks.length > 0;
  }
}

function readJsonl(filePath: string): JsonObject[] {
  if (!fs.existsSync(filePath)) return [];
  const out: JsonObject[] = [];
  for (const rawLine of fs.readFileSync(filePath, 'utf-8').split('\n')) {
    const line = rawLine.trim();
    if (!line) continue;
    try {
      out.push(JSON.parse(line) as JsonObject);
    } catch {
      continue; // 损坏行跳过（对等 py JSONDecodeError → continue）
    }
  }
  return out;
}

/** 先持久化同目录临时文件，再原子替换正式 JSONL。 */
function writeJsonl(filePath: string, rows: JsonObject[]): void {
  const parent = path.dirname(filePath);
  fs.mkdirSync(parent, { recursive: true });
  // 对等 py tempfile.mkstemp(dir=parent, prefix=f".{name}.", suffix=".tmp")：
  // 随机中缀 + 'wx' 独占创建（碰撞概率可忽略，不做 mkstemp 式重试）。
  const tempPath = path.join(
    parent,
    `.${path.basename(filePath)}.${randomBytes(6).toString('hex')}.tmp`,
  );
  const fd = fs.openSync(tempPath, 'wx');
  let fdOpen = true;
  try {
    for (const row of rows) {
      fs.writeSync(fd, `${_io.dumps(row)}\n`);
    }
    _io.fsync(fd);
    fs.closeSync(fd);
    fdOpen = false;
    _io.replace(tempPath, filePath);
  } catch (err) {
    // 写入/替换任何一步失败：关 fd、清临时文件、重抛（正式文件保持旧完整版）。
    if (fdOpen) {
      try {
        fs.closeSync(fd);
      } catch {
        // 已关闭等：对等 py contextlib.suppress(OSError)
      }
    }
    try {
      fs.unlinkSync(tempPath);
    } catch {
      // 不存在等：对等 py contextlib.suppress(OSError)
    }
    throw err;
  }
}
