/**
 * daemon 文件日志装配（对等基准 = apps/daemon tests/test_logconfig.py；B-4 可观测性）。
 *
 * 差异登记（py logging 底座 vs TS 手写滚动落盘器 src/logconfig.ts——既有底座，本文件只测不改）：
 * - py 断言 logging 内部态（handler 数 / logger.level / propagate）；TS 无对应内部面 → 一律改断
 *   **观测行为**（落盘内容 / 级别过滤 / 不写 stdout）；
 * - py env 级别名 "WARNING"（logging.getLevelName 口径）；TS 底座 LEVELS 用 "WARN"（"WARNING"
 *   不识别回落 INFO——底座既有行为）→ 本测试用 WARN 口径；
 * - py 无滚动测试（托付 stdlib RotatingFileHandler）；TS 手写滚动器 → 补滚动面；MAX_BYTES(8MB)
 *   不可注入（底座常量，禁改），用 64KB 行 × 140 逼近触发一次滚动；
 * - py 隔离靠 _isolated_root_logger 存档/还原 handler；TS 用底座提供的 resetFileLogging()。
 */

import * as fs from 'node:fs';
import * as os from 'node:os';
import * as path from 'node:path';

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getLogger, resetFileLogging, setupFileLogging } from '../src/logconfig.ts';
import { DataPaths } from '../src/paths.ts';

const MAX_BYTES = 8 * 1024 * 1024; // 与底座 logconfig.ts 常量对齐（8MB × 3 备份）

let tmp: string;

beforeEach(() => {
  tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'coagentia-logconfig-'));
  resetFileLogging();
});

afterEach(() => {
  resetFileLogging();
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  fs.rmSync(tmp, { recursive: true, force: true });
});

describe('logconfig 文件日志装配', () => {
  // py test_setup_writes_to_daemon_log
  it('装配后写入 daemon.log；命名 logger 同落盘', () => {
    const paths = new DataPaths(tmp);
    setupFileLogging(paths, 'DEBUG');
    getLogger('coagentia_daemon').debug('marker-debug-1');
    getLogger('coagentia_daemon.adapters.codex').info('codex-child-marker');
    const content = fs.readFileSync(paths.logPath, 'utf-8');
    expect(content).toContain('marker-debug-1');
    // py「子模块 logger 继承同 handler」对应面：任意命名 logger 共享单落盘器
    expect(content).toContain('codex-child-marker');
  });

  // py test_setup_is_idempotent（py 断 RotatingFileHandler 恰 1 个；TS 改断观测行为：
  // 单行只落盘一次 + 重复装配只更新级别）
  it('重复装配幂等：单落盘器不重复写，重复调用只调级别', () => {
    const paths = new DataPaths(tmp);
    setupFileLogging(paths, 'INFO');
    setupFileLogging(paths, 'INFO');
    setupFileLogging(paths, 'INFO');
    getLogger('coagentia_daemon').info('idem-marker');
    let content = fs.readFileSync(paths.logPath, 'utf-8');
    expect(content.split('idem-marker').length - 1).toBe(1); // 多次装配不重复挂落盘器（否则每行写多遍）
    setupFileLogging(paths, 'ERROR'); // 再装配 → 只更新级别
    getLogger('coagentia_daemon').info('filtered-after-relevel');
    content = fs.readFileSync(paths.logPath, 'utf-8');
    expect(content).not.toContain('filtered-after-relevel');
  });

  // py test_env_level_controls_threshold（差异：py env=WARNING；TS 级别名 WARN，见头注）
  it('env 级别控制阈值（无显式 level → 读 env）', () => {
    vi.stubEnv('COAGENTIA_DAEMON_LOG_LEVEL', 'WARN');
    const paths = new DataPaths(tmp);
    setupFileLogging(paths); // 无显式 level → 读 env
    const logger = getLogger('coagentia_daemon');
    logger.info('info-should-be-filtered');
    logger.warn('warning-should-appear');
    const content = fs.readFileSync(paths.logPath, 'utf-8');
    expect(content).toContain('warning-should-appear');
    expect(content).not.toContain('info-should-be-filtered');
  });

  // py test_explicit_level_overrides_env（py 断 logger.level；TS 改断观测行为：DEBUG 行落盘）
  it('显式 level 优先于 env', () => {
    vi.stubEnv('COAGENTIA_DAEMON_LOG_LEVEL', 'ERROR');
    const paths = new DataPaths(tmp);
    setupFileLogging(paths, 'DEBUG');
    getLogger('coagentia_daemon').debug('debug-overrides-env');
    expect(fs.readFileSync(paths.logPath, 'utf-8')).toContain('debug-overrides-env');
  });

  // py test_root_does_not_propagate（py 断 propagate=False；TS 无冒泡概念 → 改断观测面：
  // 帧原文只落盘、绝不写 stdout——防 mcp 子进程/宿主把帧打到 stdout 的同一语义）
  it('日志只落盘不写 stdout', () => {
    const spy = vi.spyOn(process.stdout, 'write');
    const paths = new DataPaths(tmp);
    setupFileLogging(paths, 'DEBUG');
    getLogger('coagentia_daemon').info('no-stdout-marker');
    const hits = spy.mock.calls.filter((c) => String(c[0]).includes('no-stdout-marker'));
    expect(hits).toEqual([]);
    expect(fs.readFileSync(paths.logPath, 'utf-8')).toContain('no-stdout-marker'); // 正控：确实落了盘
  });

  // TS 补充滚动面（py 托付 stdlib 未测；MAX_BYTES 不可注入 → 64KB 行 × 140 逼近，见头注）
  it('超 8MB 滚动出 .1 备份，当前文件重新累积', () => {
    const paths = new DataPaths(tmp);
    setupFileLogging(paths, 'DEBUG');
    const logger = getLogger('roll');
    const payload = 'x'.repeat(64 * 1024);
    for (let i = 0; i < 140; i += 1) logger.info(payload);
    expect(fs.existsSync(`${paths.logPath}.1`)).toBe(true); // 滚出备份
    expect(fs.statSync(`${paths.logPath}.1`).size).toBeLessThanOrEqual(MAX_BYTES);
    expect(fs.statSync(paths.logPath).size).toBeLessThan(MAX_BYTES); // 当前文件重新累积
  });

  // TS 补充未装配面（py 同款纪律：单测直建组件不落盘，logconfig.py 注释语义、无显式用例）
  it('未装配 getLogger 静默丢弃、不落盘不炸', () => {
    const paths = new DataPaths(tmp);
    expect(() => getLogger('coagentia_daemon').info('dropped-silently')).not.toThrow();
    expect(fs.existsSync(paths.logPath)).toBe(false);
  });
});
