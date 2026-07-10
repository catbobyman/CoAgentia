// P0a Boot 叙事屏(独立于主壳):E7 点阵背景 + E9 像素 logo + E11 boot 叙事 + E6 分段进度 + E8 像素版本号。
// 首次启动/等待工作区态;设计稿文案(品牌随 App = CoAgentia)。
import { useEffect, useState } from 'react';

import { LOGO_A_BITS } from '../lib/uiMaps';

const BRAILLE = '⠋⠙⠹⠸⠼⠴⠦⠧';

export function BootScreen() {
  const [frame, setFrame] = useState(0);
  const reduced = typeof window !== 'undefined'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  useEffect(() => {
    if (reduced) return;
    const t = setInterval(() => setFrame((f) => (f + 1) % BRAILLE.length), 80);
    return () => clearInterval(t);
  }, [reduced]);

  const spin = reduced ? '⠿' : BRAILLE[frame];

  return (
    <div className="bootscreen">
      <div className="bootwrap">
        <div className="brand">
          <div className="plogo" aria-label="CoAgentia logo">
            {LOGO_A_BITS.map((b, i) => <i key={i} className={b ? 'on' : ''} />)}
          </div>
          <div className="wordmark">COAGENTIA<span className="u">_</span></div>
        </div>

        <div className="bootlines">
          <div><span className="pr">❯</span>coagentia core ............ <span className="ok">ok</span></div>
          <div><span className="pr">❯</span>event ledger ............. <span className="ok">ok</span></div>
          <div><span className="pr">❯</span>waiting for workspace .... <span className="spin">{spin}</span></div>
        </div>

        <div className="segbar" aria-label="启动进度 6/10">
          {Array.from({ length: 10 }, (_, i) => <i key={i} className={i < 6 ? 'on' : ''} />)}
        </div>
      </div>
      <div className="bootver">VER 0.1::M1</div>
    </div>
  );
}
