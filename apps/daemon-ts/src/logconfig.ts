/**
 * daemon 进程级文件日志装配（B-4 可观测性；对等基准 = apps/daemon logconfig.py）。
 *
 * - 落盘 = `~/.coagentia/daemon/daemon.log`（DataPaths.logPath），滚动 8MB × 3 备份 + UTF-8；
 * - 级别由 env `COAGENTIA_DAEMON_LOG_LEVEL`（默认 INFO；帧原文在 DEBUG）；
 * - **只 daemon 主进程装配**（cli 非 mcp 路径）：mcp 子进程可并发多个，同写一文件会交错——故不装配；
 * - 未装配时 getLogger 返回的 logger 静默丢弃（单测直建组件不落盘，py 同款纪律）；
 * - 幂等：重复调用只调级别、不重复开新落盘器。
 *
 * 零依赖手写滚动写入器（裁决 #12：不引 pino/winston）；py RotatingFileHandler 同为同步写。
 */

import * as fs from 'node:fs';

import type { DataPaths } from './paths.ts';

const LEVELS = { DEBUG: 10, INFO: 20, WARN: 30, ERROR: 40 } as const;
export type LevelName = keyof typeof LEVELS;

const MAX_BYTES = 8 * 1024 * 1024; // 8MB × 3 备份，帧原文 DEBUG 也不至无界
const BACKUPS = 3;

function resolveLevel(level?: string | null): number {
  const name = (level ?? process.env['COAGENTIA_DAEMON_LOG_LEVEL'] ?? 'INFO').toUpperCase();
  return name in LEVELS ? LEVELS[name as LevelName] : LEVELS.INFO;
}

/** 时间戳体例对齐 py logging 默认 asctime：`YYYY-MM-DD HH:mm:ss,SSS`（本地时区）。 */
function asctime(): string {
  const d = new Date();
  const p = (n: number, w = 2) => String(n).padStart(w, '0');
  return (
    `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())},${p(d.getMilliseconds(), 3)}`
  );
}

class RotatingFileSink {
  private size: number;

  private readonly filePath: string;
  levelValue: number;

  constructor(filePath: string, levelValue: number) {
    this.filePath = filePath;
    this.levelValue = levelValue;
    this.size = fs.existsSync(filePath) ? fs.statSync(filePath).size : 0;
  }

  write(levelName: LevelName, name: string, message: string): void {
    if (LEVELS[levelName] < this.levelValue) return;
    const line = `${asctime()} ${levelName.padEnd(5)} ${name}: ${message}\n`;
    const bytes = Buffer.byteLength(line, 'utf-8');
    if (this.size + bytes > MAX_BYTES) this.rotate();
    try {
      fs.appendFileSync(this.filePath, line, 'utf-8');
      this.size += bytes;
    } catch {
      // 日志落盘失败不影响业务面（py handler 同款吞错哲学）
    }
  }

  private rotate(): void {
    try {
      for (let i = BACKUPS - 1; i >= 1; i -= 1) {
        const src = `${this.filePath}.${i}`;
        const dst = `${this.filePath}.${i + 1}`;
        if (fs.existsSync(src)) fs.renameSync(src, dst);
      }
      if (fs.existsSync(this.filePath)) fs.renameSync(this.filePath, `${this.filePath}.1`);
    } catch {
      // win32 文件被占用等：放弃本轮滚动，继续写原文件（勿丢日志）
    }
    this.size = 0;
  }
}

let sink: RotatingFileSink | null = null;

export interface Logger {
  debug(message: string): void;
  info(message: string): void;
  warn(message: string): void;
  error(message: string): void;
}

/** 幂等装配文件日志：已装配则只更新级别（对等 py setup_file_logging）。 */
export function setupFileLogging(paths: DataPaths, level?: string | null): void {
  const levelValue = resolveLevel(level);
  if (sink !== null) {
    sink.levelValue = levelValue;
    return;
  }
  fs.mkdirSync(paths.daemonDir, { recursive: true });
  sink = new RotatingFileSink(paths.logPath, levelValue);
}

/** 单测隔离用：卸下落盘器（py 侧靠 logger handler 隔离，TS 侧显式复位）。 */
export function resetFileLogging(): void {
  sink = null;
}

/** 命名空间 logger；未装配时静默丢弃（单测直建组件不落盘）。 */
export function getLogger(name: string): Logger {
  const emit = (level: LevelName, message: string) => {
    sink?.write(level, name, message);
  };
  return {
    debug: (m) => emit('DEBUG', m),
    info: (m) => emit('INFO', m),
    warn: (m) => emit('WARN', m),
    error: (m) => emit('ERROR', m),
  };
}
