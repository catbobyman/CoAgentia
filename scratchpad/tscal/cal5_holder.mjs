// cal5_holder.mjs — 持端口进程：listen 后打印 JSON 行并常驻
// 用法: node cal5_holder.mjs <port> [host|-] [exclusive:true|false|-]
import net from 'node:net';

const port = Number(process.argv[2]);
const hostArg = process.argv[3];
const host = !hostArg || hostArg === '-' ? undefined : hostArg;
const exclArg = process.argv[4];
const exclusive = !exclArg || exclArg === '-' ? undefined : exclArg === 'true';

const srv = net.createServer();
srv.on('error', (e) => {
  console.log(JSON.stringify({ event: 'error', code: e.code, message: e.message }));
  process.exit(1);
});
const opts = { port };
if (host !== undefined) opts.host = host;
if (exclusive !== undefined) opts.exclusive = exclusive;
srv.listen(opts, () => {
  console.log(
    JSON.stringify({ event: 'listening', pid: process.pid, address: srv.address(), opts })
  );
});
setInterval(() => {}, 60_000); // 常驻
