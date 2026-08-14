import { useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router";

import { workspaceById, workspaceForPath, workspaceNavigation } from "./app/workspaces";
import { AddTransactionDialog } from "./components/AddTransactionDialog";
import { FeedbackDialog } from "./components/FeedbackDialog";
import { FeedbackPage } from "./pages/FeedbackPage";
import { OverviewPage } from "./pages/OverviewPage";
import { PlaceholderPage } from "./pages/PlaceholderPage";
import { TransactionsPage } from "./pages/TransactionsPage";

function AppShell() {
  const location = useLocation();
  const navigate = useNavigate();
  const workspace = workspaceForPath(location.pathname);
  const navigation = workspaceNavigation.find((item) => item.id === workspace);
  const [feedbackRefreshKey, setFeedbackRefreshKey] = useState(0);
  const [transactionRefreshKey, setTransactionRefreshKey] = useState(0);
  const [focusTransactionId, setFocusTransactionId] = useState<string | null>(null);

  function transactionCreated(transactionId: string): void {
    setFocusTransactionId(transactionId);
    setTransactionRefreshKey((current) => current + 1);
    navigate("/transactions");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span className="brand__mark">FS</span><div><strong>家庭消费洞察</strong><span>Family Spending</span></div></div>
        <nav className="workspace-nav" aria-label="主导航">
          {workspaceNavigation.map((item) => (
            <NavLink key={item.id} to={item.path} className={({ isActive }) => `workspace-nav__item${isActive ? " workspace-nav__item--active" : ""}`}>
              <span>{item.label}</span>{!item.implemented ? <small>迁移中</small> : null}
            </NavLink>
          ))}
        </nav>
        <div className="sidebar__footer"><p>本地家庭财务工作台</p><span>数据与反馈默认保留在本机。</span></div>
      </aside>

      <div className="page-shell">
        <header className="page-header">
          <div><p className="page-header__eyebrow">{navigation?.id ?? "workspace"}</p><h1>{navigation?.label ?? "家庭消费洞察"}</h1></div>
          <div className="page-header__actions">
            <AddTransactionDialog onCreated={transactionCreated} />
            <FeedbackDialog page={location.pathname} workspace={workspace} onCreated={() => setFeedbackRefreshKey((current) => current + 1)} />
          </div>
        </header>

        <main className="page-content">
          <Routes>
            <Route path="/overview" element={<OverviewPage />} />
            <Route path="/transactions" element={<TransactionsPage refreshKey={transactionRefreshKey} focusTransactionId={focusTransactionId} />} />
            <Route path="/review" element={<PlaceholderPage workspace={workspaceById("review")} />} />
            <Route path="/automation" element={<PlaceholderPage workspace={workspaceById("automation")} />} />
            <Route path="/feedback" element={<FeedbackPage refreshKey={feedbackRefreshKey} />} />
            <Route path="/" element={<Navigate to="/overview" replace />} />
            <Route path="*" element={<Navigate to="/overview" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}

export function App() { return <AppShell />; }
