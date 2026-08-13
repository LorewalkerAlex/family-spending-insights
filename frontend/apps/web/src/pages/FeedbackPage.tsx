import {
  toFeedbackListItemViewModel,
  type FeedbackListItemViewModel,
} from "@family-spending/core";
import { useCallback, useEffect, useState } from "react";

import { familySpendingService } from "../api/client";
import { Button } from "../components/ui/Button";

interface FeedbackPageProps {
  refreshKey: number;
}

function formatCreatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function contextText(item: FeedbackListItemViewModel): string {
  const parts = [item.context.workspace, item.context.page, item.context.runtime].filter(
    (value): value is string => Boolean(value),
  );
  return parts.length > 0 ? parts.join(" · ") : "未记录页面上下文";
}

/** Feedback workspace keeps the V1 lifecycle intentionally small: open, resolve, reopen. */
export function FeedbackPage({ refreshKey }: FeedbackPageProps) {
  const [items, setItems] = useState<readonly FeedbackListItemViewModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await familySpendingService.listFeedback();
      setItems(payload.map(toFeedbackListItemViewModel));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load, refreshKey]);

  async function toggleStatus(item: FeedbackListItemViewModel): Promise<void> {
    const status = item.status === "open" ? "resolved" : "open";
    setPendingId(item.id);
    setError(null);
    try {
      const updated = await familySpendingService.updateFeedbackStatus(item.id, status);
      setItems((current) =>
        current.map((candidate) =>
          candidate.id === item.id ? toFeedbackListItemViewModel(updated) : candidate,
        ),
      );
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPendingId(null);
    }
  }

  return (
    <div className="workspace-stack">
      <section className="section-block">
        <div className="section-heading">
          <div>
            <h2>产品反馈</h2>
            <p>这里管理产品体验反馈；交易待分类和 Mapping Review 仍属于“审核”。</p>
          </div>
          <Button variant="ghost" onClick={() => void load()} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </Button>
        </div>

        {error ? <div className="inline-error">{error}</div> : null}
        {loading && items.length === 0 ? <div className="page-state">正在读取反馈…</div> : null}
        {!loading && items.length === 0 ? (
          <div className="empty-state">还没有产品反馈。可以从右上角“发送反馈”开始。</div>
        ) : null}

        <div className="feedback-list">
          {items.map((item) => (
            <article className="feedback-row" key={item.id}>
              <div className="feedback-row__body">
                <div className="feedback-row__meta">
                  <span className={`status-pill status-pill--${item.status}`}>{item.statusLabel}</span>
                  <span>{formatCreatedAt(item.createdAt)}</span>
                  <span>{contextText(item)}</span>
                </div>
                <p>{item.content}</p>
              </div>
              <Button
                variant="ghost"
                disabled={pendingId === item.id}
                onClick={() => void toggleStatus(item)}
              >
                {pendingId === item.id
                  ? "保存中…"
                  : item.status === "open"
                    ? "标记已解决"
                    : "重新打开"}
              </Button>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
