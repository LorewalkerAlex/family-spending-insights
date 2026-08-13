export type WorkspaceId =
  | "overview"
  | "transactions"
  | "review"
  | "automation"
  | "feedback";

export interface WorkspaceNavigationItem {
  id: WorkspaceId;
  path: string;
  label: string;
  description: string;
  implemented: boolean;
}

export const workspaceNavigation: readonly WorkspaceNavigationItem[] = [
  {
    id: "overview",
    path: "/overview",
    label: "概览",
    description: "家庭现金流与近期月份",
    implemented: true,
  },
  {
    id: "transactions",
    path: "/transactions",
    label: "交易",
    description: "交易查询与维护",
    implemented: false,
  },
  {
    id: "review",
    path: "/review",
    label: "审核",
    description: "待分类与 Mapping Review",
    implemented: false,
  },
  {
    id: "automation",
    path: "/automation",
    label: "自动化",
    description: "定期录入与运行状态",
    implemented: false,
  },
  {
    id: "feedback",
    path: "/feedback",
    label: "反馈",
    description: "产品反馈收集与处理",
    implemented: true,
  },
] as const;

/** Resolve one canonical item or fail loudly if navigation and route configuration drift apart. */
export function workspaceById(id: WorkspaceId): WorkspaceNavigationItem {
  const item = workspaceNavigation.find((candidate) => candidate.id === id);
  if (!item) {
    throw new Error(`Unknown workspace: ${id}`);
  }
  return item;
}

/** Resolve only canonical workspace routes; unknown paths should not invent Feedback context. */
export function workspaceForPath(pathname: string): WorkspaceId | undefined {
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, "") : pathname;
  return workspaceNavigation.find((item) => item.path === normalized)?.id;
}
