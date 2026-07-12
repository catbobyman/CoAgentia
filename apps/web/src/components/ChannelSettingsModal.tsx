// P12 频道级设置弹窗（B-M5-1，裁决 #13）。四组：基本 / 通知 / 提醒阈值 / 护栏阈值。
// 阈值·基本走既有 ChannelPatch（PATCH /channels/{id}，require_admin，单次「保存」批量提交差异）；
// 通知走 notification-setting（人类本人自治，即点即存 + 本地更新快照）。编排/Project 组归 M6，不做。
// DM 频道：无通知设置面（裁决 #5 DM 必达，422 NOTIF_IN_DM）——通知组隐藏。
import { useState } from 'react';
import { Bell, Save, Shield, SlidersHorizontal, Timer } from 'lucide-react';

import type { ChannelPatch, ChannelPublic, NotificationMode } from '@coagentia/contracts-ts';

import { usePatchChannel, usePutNotificationSetting } from '../data/queries';
import { useToast } from './Toast';
import { ApiError } from '../api';
import { ProjectSettingsSection } from './ProjectSettingsSection';
import './channel-settings.css';

// 数字列 → 输入串（null/undefined = 继承工作区默认 → 空串）。
const numStr = (v: number | null | undefined): string => (v == null ? '' : String(v));

// 校验后的非负数值，无效 → undefined。
function parseNum(s: string): number | undefined {
  const t = s.trim();
  if (t === '') return undefined;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

const NOTIFY_OPTS: { mode: NotificationMode; label: string; hint: string }[] = [
  { mode: 'all', label: '全部', hint: '所有新消息都点亮/推送' },
  { mode: 'mentions', label: '仅 @', hint: '仅被 @ 时点亮/推送' },
  { mode: 'mute', label: '静音', hint: '不点亮徽标、不推送（未读事实仍保留）' },
];

export function ChannelSettingsModal({ channel, meId, currentMode, canManageProjects = true, onClose }: {
  channel: ChannelPublic;
  meId: string | undefined;
  currentMode: NotificationMode;
  canManageProjects?: boolean;
  onClose: () => void;
}) {
  const toast = useToast();
  const patchM = usePatchChannel();
  const notifM = usePutNotificationSetting(meId);
  const isDm = channel.kind === 'dm';

  // 基本
  const [desc, setDesc] = useState(channel.description ?? '');
  const [priv, setPriv] = useState(!!channel.is_private);
  // 提醒阈值
  const [todoH, setTodoH] = useState(numStr(channel.remind_todo_h));
  const [inprogH, setInprogH] = useState(numStr(channel.remind_inprog_h));
  const [reviewH, setReviewH] = useState(numStr(channel.remind_review_h));
  const [escalation, setEscalation] = useState(!!channel.remind_escalation);
  // 护栏阈值
  const [reevalMin, setReevalMin] = useState(numStr(channel.held_reeval_min));
  const [escalateN, setEscalateN] = useState(numStr(channel.held_escalate_n));
  // 通知 mode（即点即存的本地态）
  const [mode, setMode] = useState<NotificationMode>(currentMode);

  const addNum = (
    patch: ChannelPatch,
    key: 'remind_todo_h' | 'remind_inprog_h' | 'remind_review_h' | 'held_reeval_min' | 'held_escalate_n',
    val: string,
    orig: number | null | undefined,
  ) => {
    const n = parseNum(val);
    if (n !== undefined && n !== (orig ?? undefined)) patch[key] = n;
  };

  // 差异化 patch：仅提交与原值不同的字段（null 会被 server 忽略，故只送有效变更）。
  const buildPatch = (): ChannelPatch => {
    const patch: ChannelPatch = {};
    if (desc !== (channel.description ?? '')) patch.description = desc;
    if (priv !== !!channel.is_private) patch.is_private = priv;
    addNum(patch, 'remind_todo_h', todoH, channel.remind_todo_h);
    addNum(patch, 'remind_inprog_h', inprogH, channel.remind_inprog_h);
    addNum(patch, 'remind_review_h', reviewH, channel.remind_review_h);
    addNum(patch, 'held_reeval_min', reevalMin, channel.held_reeval_min);
    addNum(patch, 'held_escalate_n', escalateN, channel.held_escalate_n);
    if (escalation !== !!channel.remind_escalation) patch.remind_escalation = escalation;
    return patch;
  };

  const save = () => {
    const patch = buildPatch();
    if (Object.keys(patch).length === 0) { onClose(); return; }
    patchM.mutate(
      { channelId: channel.id, patch },
      {
        onSuccess: () => { toast.push('频道设置已保存', { tone: 'success' }); onClose(); },
        onError: (e: unknown) =>
          toast.push(e instanceof ApiError ? e.message : '保存频道设置失败', { tone: 'error' }),
      },
    );
  };

  // 通知 mode：即点即存（本人自治）。乐观置本地态；失败由 mutation 弹 toast 并回退。
  const pickMode = (next: NotificationMode) => {
    if (next === mode) return;
    const prev = mode;
    setMode(next);
    notifM.mutate({ channelId: channel.id, mode: next }, { onError: () => setMode(prev) });
  };

  return (
    <div className="scrim" onClick={onClose}>
      <div className="modal chsettings" onClick={(e) => e.stopPropagation()} data-testid="channel-settings">
        <div className="mtitle">频道设置 · #{channel.name}</div>

        {/* 基本 */}
        <div className="cs-sec">
          <div className="cs-label"><SlidersHorizontal />基本</div>
          <div className="cs-card">
            <div className="cs-row">
              <div className="cs-lb"><div className="t">描述</div></div>
              <div className="cs-ctl grow">
                <div className="inp"><input className="val" value={desc} placeholder="频道用途…" aria-label="频道描述" onChange={(e) => setDesc(e.target.value)} /></div>
              </div>
            </div>
            <div className="cs-row">
              <div className="cs-lb"><div className="t">可见性</div><div className="d">私有频道仅成员可见</div></div>
              <div className="cs-ctl">
                <div className="cs-seg" role="group" aria-label="可见性">
                  <button type="button" className={priv ? '' : 'active'} onClick={() => setPriv(false)}>公开</button>
                  <button type="button" className={priv ? 'active' : ''} onClick={() => setPriv(true)}>私有</button>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* 通知（DM 无设置面） */}
        {!isDm && (
          <div className="cs-sec">
            <div className="cs-label"><Bell />通知</div>
            <div className="cs-card">
              <div className="cs-row">
                <div className="cs-lb"><div className="t">通知级别</div><div className="d">{NOTIFY_OPTS.find((o) => o.mode === mode)?.hint}</div></div>
                <div className="cs-ctl">
                  <div className="cs-seg" role="radiogroup" aria-label="通知级别">
                    {NOTIFY_OPTS.map((o) => (
                      <button
                        key={o.mode}
                        type="button"
                        role="radio"
                        aria-checked={mode === o.mode}
                        className={mode === o.mode ? 'active' : ''}
                        disabled={notifM.isPending}
                        onClick={() => pickMode(o.mode)}
                      >{o.label}</button>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          </div>
        )}

        {!isDm && (
          <ProjectSettingsSection channelId={channel.id} canManage={canManageProjects} />
        )}

        {/* 提醒阈值 */}
        <div className="cs-sec">
          <div className="cs-label"><Timer />提醒阈值</div>
          <div className="cs-card">
            <NumRow label="Todo 沉默" hint="超时未认领后提醒" value={todoH} unit="h" onChange={setTodoH} />
            <NumRow label="In Progress 沉默" hint="进行中无进展后提醒" value={inprogH} unit="h" onChange={setInprogH} />
            <NumRow label="In Review 沉默" hint="待评审停滞后提醒" value={reviewH} unit="h" onChange={setReviewH} />
            <div className="cs-row">
              <div className="cs-lb"><div className="t">升级链</div><div className="d">二次提醒无响应则升级 @人类</div></div>
              <div className="cs-ctl">
                <button
                  type="button"
                  className={`cs-toggle${escalation ? ' on' : ''}`}
                  role="switch"
                  aria-checked={escalation}
                  aria-label="提醒升级链"
                  onClick={() => setEscalation((v) => !v)}
                ><span className="knob" /></button>
              </div>
            </div>
          </div>
        </div>

        {/* 护栏阈值 */}
        <div className="cs-sec">
          <div className="cs-label"><Shield />护栏阈值</div>
          <div className="cs-card">
            <NumRow label="重评估等待" hint="G4 · HeldDraft 自动重评估等待时长" value={reevalMin} unit="min" onChange={setReevalMin} />
            <NumRow label="升级次数" hint="G5 · 连续被扣达此次数升级 @人类" value={escalateN} unit="次" onChange={setEscalateN} />
          </div>
        </div>

        <div className="ops">
          <button className="btn btn-ghost" onClick={onClose}>关闭</button>
          <button className="btn btn-primary" disabled={patchM.isPending} onClick={save}><Save />保存</button>
        </div>
      </div>
    </div>
  );
}

function NumRow({ label, hint, value, unit, onChange }: {
  label: string;
  hint: string;
  value: string;
  unit: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="cs-row">
      <div className="cs-lb"><div className="t">{label}</div><div className="d">{hint}</div></div>
      <div className="cs-ctl">
        <span className="cs-numinp">
          <input
            inputMode="numeric"
            value={value}
            aria-label={label}
            placeholder="默认"
            onChange={(e) => onChange(e.target.value)}
          />
          <span className="unit">{unit}</span>
        </span>
      </div>
    </div>
  );
}
