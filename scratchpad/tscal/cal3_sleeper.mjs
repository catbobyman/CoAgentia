// cal3_sleeper.mjs — 叶子进程：写自身 pid 到指定文件后长眠（120s 自杀保险）
import { writeFileSync } from 'node:fs';
const pidFile = process.argv[2];
if (pidFile) writeFileSync(pidFile, String(process.pid));
setTimeout(() => process.exit(0), 120_000);
