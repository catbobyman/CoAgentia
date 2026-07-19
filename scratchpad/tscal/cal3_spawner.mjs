// cal3_spawner.mjs — 短命中介：detached 起 sleeper 后立即退出（制造孤儿）
import { spawn } from 'node:child_process';
const [, , sleeperPath, pidFile] = process.argv;
const c = spawn(process.execPath, [sleeperPath, pidFile], { detached: true, stdio: 'ignore' });
c.unref();
