// TanStack Router 路由树 + 类型化 search schema(选型 00 约束 6:URL 即状态)。
// 路由树:
//   root(裸 Outlet)
//   ├─ appLayout(RootLayout 壳:Rail + 频道侧栏 + WS)—— 复用壳的主应用屏
//   │   ├─ index '/'          会话屏(P1;?tab=&thread=&task= 深链)
//   │   ├─ /agents/$memberId  Agent 详情(P6;?tab= profile/home/skills/…)
//   │   └─ /computers         机器(P7)
//   ├─ /boot                  Boot 叙事(P0a;独立于主壳)
//   ├─ /create-workspace      创建工作区(P0b;独立于主壳)
//   └─ /setup                 起步清单(P0c;首跑态,自带仅 #all 侧栏)
// P1 行为不回退:index 仍经 RootLayout 壳的 <Outlet/> 渲染,只是壳从 root 下沉为 pathless layout。
import {
  createRootRoute, createRoute, createRouter, useNavigate, useParams, useSearch,
} from '@tanstack/react-router';

import { RootLayout } from './routes/RootLayout';
import { ChannelChatScreen } from './screens/ChannelChatScreen';
import { AgentDetailScreen } from './screens/AgentDetailScreen';
import { ComputersScreen } from './screens/ComputersScreen';
import { WorktreesScreen } from './screens/WorktreesScreen';
import { WorkspaceBoardScreen } from './screens/WorkspaceBoardScreen';
import { MembersScreen } from './screens/MembersScreen';
import { ActivityScreen } from './screens/ActivityScreen';
import { BootScreen } from './screens/BootScreen';
import { CreateWorkspaceScreen } from './screens/CreateWorkspaceScreen';
import { SetupChecklistScreen } from './screens/SetupChecklistScreen';
import {
  validateChannelSearch, validateAgentSearch,
  type ChannelSearch, type AgentSearch,
} from './routes/search';

const rootRoute = createRootRoute(); // 无 component → 默认渲染 <Outlet/>(裸容器)

// pathless layout:承载 B1 布局壳(Rail + 频道侧栏 + WS 生命周期)。
const appLayoutRoute = createRoute({
  getParentRoute: () => rootRoute,
  id: 'app',
  component: RootLayout,
});

const indexRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/',
  // 类型化 search:任意 URL 输入 → 还原 { tab, thread?, task? }
  validateSearch: (input: Record<string, unknown>): ChannelSearch => validateChannelSearch(input),
  component: IndexScreen,
});

function IndexScreen() {
  const search = useSearch({ from: '/app/' });
  const navigate = useNavigate();
  // 绝对导航合并当前 search(避免 pathless layout 下 from-相对更新的类型摩擦)。
  const setSearch = (next: Partial<ChannelSearch>) =>
    void navigate({ to: '/', search: { ...search, ...next } });
  return <ChannelChatScreen search={search} setSearch={setSearch} />;
}

const agentRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/agents/$memberId',
  validateSearch: (input: Record<string, unknown>): AgentSearch => validateAgentSearch(input),
  component: AgentScreen,
});

function AgentScreen() {
  const { memberId } = useParams({ from: '/app/agents/$memberId' });
  const search = useSearch({ from: '/app/agents/$memberId' });
  const navigate = useNavigate();
  const setTab = (tab: AgentSearch['tab']) =>
    void navigate({ to: '/agents/$memberId', params: { memberId }, search: { tab } });
  // key=memberId：切换 Agent 时整棵子树重挂载，避免 ProfileTab 就地编辑草稿（editDesc/descDraft）
  // 跨 Agent 残留、把编辑存到错的 Agent 上（code-review 修）。
  return <AgentDetailScreen key={memberId} memberId={memberId} tab={search.tab} setTab={setTab} />;
}

const computersRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/computers',
  component: ComputersScreen,
});

// PS-WT ② 工作树管理台（顶级屏，复用主壳，无深链 search，照 computersRoute 范式）。
const worktreesRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/worktrees',
  component: WorktreesScreen,
});

// M2 工作区级表面(P11/P8/P9),复用主壳,无深链 search(照 computersRoute 范式)。
const tasksRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/tasks',
  component: WorkspaceBoardScreen,
});
const membersRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/members',
  component: MembersScreen,
});
const activityRoute = createRoute({
  getParentRoute: () => appLayoutRoute,
  path: '/activity',
  component: ActivityScreen,
});

// ---- 独立于主壳的首跑/工作区流程(P0a/P0b/P0c)
const bootRoute = createRoute({
  getParentRoute: () => rootRoute, path: '/boot', component: BootScreen,
});
const createWorkspaceRoute = createRoute({
  getParentRoute: () => rootRoute, path: '/create-workspace', component: CreateWorkspaceScreen,
});
const setupRoute = createRoute({
  getParentRoute: () => rootRoute, path: '/setup', component: SetupChecklistScreen,
});

const routeTree = rootRoute.addChildren([
  appLayoutRoute.addChildren([
    indexRoute, agentRoute, computersRoute, worktreesRoute, tasksRoute, membersRoute, activityRoute,
  ]),
  bootRoute,
  createWorkspaceRoute,
  setupRoute,
]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
