// F4 工作区设置弹窗（A5 死壳补齐）：主题 / 桌面通知 / 提示音 / 欢迎语 / 附件上限五项（WorkspacePatch）。
// 照 ChannelSettingsModal 体例（cs-sec/cs-card/cs-row/cs-seg/cs-toggle/cs-numinp），单次「保存」批量提交
// 差异（parseNum 越界不提交）。主题在弹窗内即时预览（applyTheme），取消则回滚到落库值；桌面通知/声音
// 落 workspace（desktopNotify.ts 读的就是这组配置，改开关即生效）。
import { useState } from 'react';
import { Bell, Palette, Save, SlidersHorizontal } from 'lucide-react';

import type { UiTheme, WorkspacePatch, WorkspacePublic } from '@coagentia/contracts-ts';

import { usePatchWorkspace } from '../data/queries';
import { applyTheme } from '../lib/theme';
import { useToast } from './Toast';
import { ApiError } from '../api';
import './channel-settings.css';

const THEME_OPTS: { key: UiTheme; label: string }[] = [
  { key: 'dark', label: '深色' },
  { key: 'light', label: '浅色' },
  { key: 'system', label: '跟随系统' },
];

/** 非负整数解析（附件上限 MB），无效 → undefined（不提交）。 */
function parseMb(s: string): number | undefined {
  const t = s.trim();
  if (t === '') return undefined;
  const n = Number(t);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

export function WorkspaceSettingsModal({ workspace, onClose }: {
  workspace: WorkspacePublic;
  onClose: () => void;
}) {
  const toast = useToast();
  const patchM = usePatchWorkspace();

  const original: UiTheme = workspace.ui_theme ?? 'dark';
  const [theme, setTheme] = useState<UiTheme>(original);
  const [notifDesktop, setNotifDesktop] = useState(!!workspace.notif_desktop);
  const [notifSound, setNotifSound] = useState(!!workspace.notif_sound);
  const [greeting, setGreeting] = useState(!!workspace.onboarding_greeting);
  const [attachMb, setAttachMb] = useState(String(workspace.attachment_max_mb ?? ''));

  // 主题即时预览：改选即 applyTheme（不落库，直到「保存」）。
  const pickTheme = (next: UiTheme) => {
    setTheme(next);
    applyTheme(next);
  };
  // 取消 = 回滚预览到落库值 + 关闭。
  const cancel = () => {
    applyTheme(original);
    onClose();
  };

  const buildPatch = (): WorkspacePatch => {
    const patch: WorkspacePatch = {};
    if (theme !== workspace.ui_theme) patch.ui_theme = theme;
    if (notifDesktop !== !!workspace.notif_desktop) patch.notif_desktop = notifDesktop;
    if (notifSound !== !!workspace.notif_sound) patch.notif_sound = notifSound;
    if (greeting !== !!workspace.onboarding_greeting) patch.onboarding_greeting = greeting;
    const mb = parseMb(attachMb);
    if (mb !== undefined && mb !== workspace.attachment_max_mb) patch.attachment_max_mb = mb;
    return patch;
  };

  const save = () => {
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) { onClose(); return; }
    patchM.mutate(patch, {
      onSuccess: () => { toast.push('工作区设置已保存', { tone: 'success' }); onClose(); },
      onError: (e: unknown) => {
        // 保存失败：主题回滚到落库值（避免预览与库不一致）。
        applyTheme(original);
        setTheme(original);
        toast.push(e instanceof ApiError ? e.message : '保存工作区设置失败', { tone: 'error' });
      },
    });
  };

  return (
    <div className="scrim" onClick={cancel}>
      <div className="modal chsettings" onClick={(e) => e.stopPropagation()} data-testid="workspace-settings">
        <div className="mtitle">工作区设置</div>

        {/* 外观 */}
        <div className="cs-sec">
          <div className="cs-label"><Palette />外观</div>
          <div className="cs-card">
            <div className="cs-row">
              <div className="cs-lb"><div className="t">主题</div><div className="d">深浅/跟随系统（即时预览）</div></div>
              <div className="cs-ctl">
                <div className="cs-seg" role="radiogroup" aria-label="主题">
                  {THEME_OPTS.map((o) => (
                    <button
                      key={o.key}
                      type="button"
                      role="radio"
                      aria-checked={theme === o.key}
                      className={theme === o.key ? 'active' : ''}
                      onClick={() => pickTheme(o.key)}
                    >{o.label}</button>
                  ))}
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 通知 */}
        <div className="cs-sec">
          <div className="cs-label"><Bell />通知</div>
          <div className="cs-card">
            <ToggleRow
              label="桌面通知" hint="新消息弹系统通知（需浏览器已授权）"
              on={notifDesktop} ariaLabel="桌面通知" onToggle={() => setNotifDesktop((v) => !v)}
            />
            <ToggleRow
              label="提示音" hint="新消息播放提示音"
              on={notifSound} ariaLabel="提示音" onToggle={() => setNotifSound((v) => !v)}
            />
          </div>
        </div>

        {/* 其它 */}
        <div className="cs-sec">
          <div className="cs-label"><SlidersHorizontal />其它</div>
          <div className="cs-card">
            <ToggleRow
              label="欢迎语" hint="新成员/新频道自动发欢迎消息"
              on={greeting} ariaLabel="欢迎语" onToggle={() => setGreeting((v) => !v)}
            />
            <div className="cs-row">
              <div className="cs-lb"><div className="t">附件上限</div><div className="d">单文件最大体积</div></div>
              <div className="cs-ctl">
                <span className="cs-numinp">
                  <input
                    inputMode="numeric" value={attachMb} aria-label="附件上限"
                    placeholder="200" onChange={(e) => setAttachMb(e.target.value)}
                  />
                  <span className="unit">MB</span>
                </span>
              </div>
            </div>
          </div>
        </div>

        <div className="ops">
          <button className="btn btn-ghost" onClick={cancel}>取消</button>
          <button className="btn btn-primary" disabled={patchM.isPending} onClick={save}><Save />保存</button>
        </div>
      </div>
    </div>
  );
}

function ToggleRow({ label, hint, on, ariaLabel, onToggle }: {
  label: string; hint: string; on: boolean; ariaLabel: string; onToggle: () => void;
}) {
  return (
    <div className="cs-row">
      <div className="cs-lb"><div className="t">{label}</div><div className="d">{hint}</div></div>
      <div className="cs-ctl">
        <button
          type="button" className={`cs-toggle${on ? ' on' : ''}`}
          role="switch" aria-checked={on} aria-label={ariaLabel}
          onClick={onToggle}
        ><span className="knob" /></button>
      </div>
    </div>
  );
}
