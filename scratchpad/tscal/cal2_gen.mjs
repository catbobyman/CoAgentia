// cal2_gen.mjs — 确定性单行 JSON 帧生成器（child 与 reader 共用，保证期望值逐字节可复算）
// spec 语法: "8" => 8MB 纯 ASCII 内容; "u1" => 1MB 含中文+emoji 多字节内容
export function makeFrame(spec) {
  const multibyte = spec.startsWith('u');
  const mb = Number(multibyte ? spec.slice(1) : spec);
  if (!Number.isFinite(mb) || mb <= 0) throw new Error(`bad spec: ${spec}`);
  const target = mb * 1048576; // 行内容总字节数（不含结尾 \n），精确到字节
  const header = Buffer.from(`{"type":"frame","spec":"${spec}","data":"`, 'utf8');
  const footer = Buffer.from('"}', 'utf8');
  const dataLen = target - header.length - footer.length;
  if (dataLen < 0) throw new Error('frame too small');
  const unit = Buffer.from(
    multibyte ? '中文测试大帧🚀数据流校准' : 'abcdefghijklmnopqrstuvwxyz0123456789',
    'utf8',
  );
  const data = Buffer.alloc(dataLen, 0x78); // 先全 'x' 垫底（尾部残留保持 ASCII，避免切断多字节字符）
  let off = 0;
  while (off + unit.length <= dataLen) {
    unit.copy(data, off);
    off += unit.length;
  }
  const frame = Buffer.concat([header, data, footer]);
  if (frame.length !== target) throw new Error(`frame length mismatch: ${frame.length} != ${target}`);
  return frame;
}
