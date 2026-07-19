// cal5_try.mjs — 第二进程尝试 listen 同端口，打印结果 JSON 后退出
// 用法: node cal5_try.mjs <port> [host|-] [exclusive:true|false|-] [reusePort:true|-]
import net from 'node:net';

const port = Number(process.argv[2]);
const hostArg = process.argv[3];
const host = !hostArg || hostArg === '-' ? undefined : hostArg;
const exclArg = process.argv[4];
const exclusive = !exclArg || exclArg === '-' ? undefined : exclArg === 'true';
const reusePort = process.argv[5] === 'true' ? true : undefined;

const t0 = process.hrtime.bigint();
const srv = net.createServer();
srv.on('error', (e) => {
  const ms = Number(process.hrtime.bigint() - t0) / 1e6;
  console.log(
    JSON.stringify({ result: 'REJECTED', code: e.code, message: e.message, ms: +ms.toFixed(2) })
  );
  process.exit(0);
});
const opts = { port };
if (host !== undefined) opts.host = host;
if (exclusive !== undefined) opts.exclusive = exclusive;
if (reusePort !== undefined) opts.reusePort = reusePort;
try {
  srv.listen(opts, () => {
    const ms = Number(process.hrtime.bigint() - t0) / 1e6;
    console.log(
      JSON.stringify({ result: 'BOUND', address: srv.address(), opts, ms: +ms.toFixed(2) })
    );
    srv.close(() => process.exit(0));
  });
} catch (e) {
  console.log(JSON.stringify({ result: 'THROW', code: e.code, message: e.message }));
  process.exit(0);
}
