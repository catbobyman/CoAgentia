// cal2_child.mjs — 子进程：按 argv 的 spec 序列，向 stdout 逐帧写「单行超大 JSON + \n」
// 模拟 claude CLI stream-json 的大帧输出（含背压处理）
import { makeFrame } from './cal2_gen.mjs';

const specs = process.argv.slice(2);
const NL = Buffer.from('\n');

async function writeAll(buf) {
  if (!process.stdout.write(buf)) {
    await new Promise((r) => process.stdout.once('drain', r));
  }
}

for (const spec of specs) {
  const frame = makeFrame(spec);
  await writeAll(frame);
  await writeAll(NL);
}
