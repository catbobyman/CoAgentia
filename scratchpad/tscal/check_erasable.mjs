// 全量检出 daemon-ts src 内不可剥离 TS 语法（参数属性/enum/namespace）：逐文件真 import。
import { readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const ROOT = process.argv[2];
const files = [];
(function walk(d) {
  for (const n of readdirSync(d)) {
    const p = join(d, n);
    if (statSync(p).isDirectory()) walk(p);
    else if (n.endsWith('.ts')) files.push(p);
  }
})(ROOT);

let bad = 0;
for (const f of files) {
  try {
    await import(pathToFileURL(f).href);
  } catch (e) {
    const s = String(e && e.code ? e.code : e);
    if (s.includes('TYPESCRIPT_SYNTAX') || String(e).includes('TYPESCRIPT_SYNTAX')) {
      console.log('SYNTAX-FAIL:', f);
      console.log('  ', String(e.message).split('\n').slice(-2).join(' | '));
      bad += 1;
    }
    // 其它运行期错误（如顶层副作用）不算语法问题
  }
}
console.log(bad === 0 ? 'ALL-ERASABLE' : `BAD=${bad}`);
