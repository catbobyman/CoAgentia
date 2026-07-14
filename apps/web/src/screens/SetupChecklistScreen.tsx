// P0c 起步清单(首跑态,独立于主壳):#all 空频道 + SETUP 终端卡三步依赖链。
// 首跑态侧栏仅 #all(复发点 4 的例外);勾选态读 workspace.setup_state。
import { useState } from 'react';
import { Ellipsis, Lock, Plus } from 'lucide-react';
import { useNavigate } from '@tanstack/react-router';

import { channelsOf, useChannelsSnapshot, useMembers, useWorkspace } from '../data/queries';
import { useUiStore } from '../lib/store';
import { Rail } from '../components/Rail';
import { TemplateWizard } from '../components/TemplateWizard';
import { ToastProvider, Toaster } from '../components/Toast';

interface StepDef {
  no: string; txt: string; action: string; key: 'add_computer' | 'create_agent' | 'first_task';
  dep?: string; onAction?: () => void;
}

export function SetupChecklistScreen() {
  const wsQ = useWorkspace();
  const channelsQ = useChannelsSnapshot();
  const membersQ = useMembers();
  const navigate = useNavigate();
  const setActiveChannel = useUiStore((s) => s.setActiveChannel);
  const setup = (wsQ.data?.setup_state ?? {}) as Record<string, boolean>;
  const [wizardOpen, setWizardOpen] = useState(false);

  const channels = channelsOf(channelsQ.data);
  const members = membersQ.data ?? [];
  // 向导实例化目标 = #all(首跑态唯一频道)；缺则取首个频道。
  const targetChannel = channels.find((c) => c.name === 'all') ?? channels[0];

  const steps: StepDef[] = [
    { no: '001', txt: '连接一台机器', action: 'Add Computer', key: 'add_computer',
      onAction: () => void navigate({ to: '/computers' }) },
    { no: '002', txt: '创建第一个 Agent', action: '创建 Agent', key: 'create_agent', dep: '001' },
    { no: '003', txt: '发第一条消息或从模板开始', action: '打开模板向导', key: 'first_task', dep: '002',
      onAction: () => { if (targetChannel) setWizardOpen(true); } },
  ];

  const done = (k: StepDef['key']) => setup[k] === true;
  const depDone = (i: number) => i === 0 || done(steps[i - 1].key);

  return (
    // 首跑态整屏套 ToastProvider（Rail 的主题切换/未来 toast 依赖它；原仅包 TemplateWizard）。
    <ToastProvider>
    <div className="app app--setup">
      {/* rail 复用(首跑态) */}
      <Rail meName="M" />

      {/* 侧栏:仅 #all(首跑态例外) */}
      <aside className="chlist">
        <div className="grp">Channels</div>
        <div className="ch active"><span className="hash">#</span><span className="nm">all</span></div>
        <div className="sp" />
        <div className="newch"><Plus /><span>新建频道</span></div>
      </aside>

      <main className="main">
        <header className="topbar">
          <span className="cname"><span className="hash">#</span>all</span>
          <span className="cdesc">工作区默认频道——所有成员都在这里</span>
          <div className="stack" aria-label="频道成员(1)">
            <span className="sav" style={{ background: 'var(--avatar-3)', borderRadius: 'var(--radius-round)' }}>M</span>
            <span className="n">1</span>
          </div>
          {/* F12：首跑屏是引导示意，非功能面——假元素降级为明确的示意态（去可点、低饱和）。 */}
          <div className="icobtn setup-demo" aria-hidden="true"><Ellipsis /></div>
        </header>
        <nav className="tabs setup-demo" aria-hidden="true">
          <div className="tab active">会话</div>
          <div className="tab">画布</div>
          <div className="tab">看板</div>
          <div className="tab">文件</div>
        </nav>

        <section className="flow">
          <div className="tcard-wrap">
            <span className="tcard-label">─ SETUP ─</span>
            <div className="tcard">
              {steps.map((s, i) => {
                const isDone = done(s.key);
                const locked = !isDone && !depDone(i);
                const actionable = !isDone && !locked;
                return (
                  <div className={`step${locked ? ' locked' : ''}`} key={s.no}>
                    <span className="no">{s.no}</span>
                    <span className="txt">{s.txt}</span>
                    <button
                      className={`btn ${actionable ? 'btn-primary' : 'btn-secondary'}`}
                      disabled={!actionable}
                      onClick={s.onAction}
                    >{s.action}</button>
                    <span className="st">
                      {isDone
                        ? <>✓ 已完成</>
                        : locked
                          ? <><Lock />依赖 {s.dep}</>
                          : <>⠿ 待完成</>}
                    </span>
                  </div>
                );
              })}
              <div className="ft"><button className="btn btn-ghost setup-demo" aria-hidden="true">收起</button></div>
            </div>
          </div>
        </section>

        {/* F12：首跑屏假编辑器/假发送降级为示意态（真发消息在完成三步、进入会话屏后可用）。 */}
        <footer className="composer setup-demo" aria-hidden="true">
          <div className="combox">
            <span className="prompt">❯</span>
            <span className="line ph">发消息到 #all…</span>
            <label className="astask"><span className="box" />As Task</label>
            <button className="btn btn-primary">发送</button>
          </div>
        </footer>
      </main>

      {/* 模板向导(首跑态在主壳之外——共用整屏 ToastProvider，mutation 的 toast 依赖它)。 */}
      {wizardOpen && targetChannel && (
        <TemplateWizard
          channelId={targetChannel.id}
          members={members}
          onClose={() => setWizardOpen(false)}
          onInstantiated={(channelId) => {
            setWizardOpen(false);
            setActiveChannel(channelId);
            void navigate({ to: '/', search: { tab: 'canvas' } });
          }}
        />
      )}
      <Toaster />
    </div>
    </ToastProvider>
  );
}
