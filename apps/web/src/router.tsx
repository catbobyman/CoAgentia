// TanStack Router 路由树 + 类型化 search schema(选型 00 约束 6:URL 即状态)。
// 路由树:root(布局壳)→ index '/'(会话屏,带 ?tab=&thread=&task=&node= 深链)。
// M1 只有 index 一条子路由;B2 其它屏挂同一 root 下即复用布局壳。
import {
  createRootRoute, createRoute, createRouter, useNavigate, useSearch,
} from '@tanstack/react-router';

import { RootLayout } from './routes/RootLayout';
import { ChannelChatScreen } from './screens/ChannelChatScreen';
import { validateChannelSearch, type ChannelSearch } from './routes/search';

const rootRoute = createRootRoute({ component: RootLayout });

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: '/',
  // 类型化 search:任意 URL 输入 → 还原 { tab, thread?, task?, node? }
  validateSearch: (input: Record<string, unknown>): ChannelSearch => validateChannelSearch(input),
  component: IndexScreen,
});

function IndexScreen() {
  const search = useSearch({ from: indexRoute.id });
  const navigate = useNavigate({ from: indexRoute.id });
  const setSearch = (next: Partial<ChannelSearch>) =>
    void navigate({ search: (prev) => ({ ...prev, ...next }) });
  return <ChannelChatScreen search={search} setSearch={setSearch} />;
}

const routeTree = rootRoute.addChildren([indexRoute]);

export const router = createRouter({ routeTree });

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
}
