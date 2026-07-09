// 页签条(36px):会话/画布/看板/文件。active 由深链 ?tab= 驱动;点击回写 URL(深链还原闭环)。
import type { Tab } from '../routes/search';

const TAB_LABELS: Record<Tab, string> = {
  chat: '会话', canvas: '画布', board: '看板', files: '文件',
};

export function Tabs({ active, canvasCount, boardCount, onSelect }: {
  active: Tab;
  canvasCount?: number;
  boardCount?: number;
  onSelect: (tab: Tab) => void;
}) {
  const counts: Partial<Record<Tab, number | undefined>> = {
    canvas: canvasCount, board: boardCount,
  };
  return (
    <nav className="tabs">
      {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => {
        const cnt = counts[tab];
        return (
          <div
            key={tab}
            className={`tab${tab === active ? ' active' : ''}`}
            onClick={() => onSelect(tab)}
          >
            {TAB_LABELS[tab]}
            {cnt !== undefined && cnt > 0 && <span className="cnt">{cnt}</span>}
          </div>
        );
      })}
    </nav>
  );
}
