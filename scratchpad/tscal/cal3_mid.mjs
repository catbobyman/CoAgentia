// cal3_mid.mjs — 中间进程（模拟 npm 壳里的 node）：写自身 pid，再起 sleeper 孙进程
import { writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const [, , midPidFile, sleeperPidFile, mode] = process.argv;
writeFileSync(midPidFile, String(process.pid));
const here = dirname(fileURLToPath(import.meta.url));
const sleeper = join(here, 'cal3_sleeper.mjs');

if (mode === 'orphan') {
  // 经短命 spawner detached 起 sleeper，spawner 退出后 sleeper 成孤儿（父 pid 已死）
  spawn(process.execPath, [join(here, 'cal3_spawner.mjs'), sleeper, sleeperPidFile], { stdio: 'ignore' });
} else {
  spawn(process.execPath, [sleeper, sleeperPidFile], { stdio: 'ignore' });
}
setTimeout(() => process.exit(0), 120_000);
