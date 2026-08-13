import type { WorkspaceNavigationItem } from "../app/workspaces";

interface PlaceholderPageProps {
  workspace: WorkspaceNavigationItem;
}

/** Explicit migration state prevents a placeholder from masquerading as a finished product surface. */
export function PlaceholderPage({ workspace }: PlaceholderPageProps) {
  return (
    <section className="placeholder-panel">
      <span className="migration-badge">迁移中</span>
      <h2>{workspace.label}</h2>
      <p>{workspace.description} 将在后续完整纵向切片接入；当前不会通过 iframe 或半迁移方式复用旧 Dashboard。</p>
    </section>
  );
}
