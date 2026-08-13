import { Button, Text, View } from "@tarojs/components";
import {
  toFeedbackListItemViewModel,
  type FeedbackListItemViewModel,
} from "@family-spending/core";
import { useCallback, useEffect, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";

function formatCreatedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  const parts = [
    date.getFullYear(),
    String(date.getMonth() + 1).padStart(2, "0"),
    String(date.getDate()).padStart(2, "0"),
  ];
  const time = [String(date.getHours()).padStart(2, "0"), String(date.getMinutes()).padStart(2, "0")];
  return `${parts.join("-")} ${time.join(":")}`;
}

/** More hosts Mini-only secondary surfaces; Feedback is real while Automation remains an explicit placeholder. */
export default function MorePage() {
  const [items, setItems] = useState<readonly FeedbackListItemViewModel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [pendingId, setPendingId] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);

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
    setPendingId(item.id);
    setError(null);
    try {
      const updated = await familySpendingService.updateFeedbackStatus(
        item.id,
        item.status === "open" ? "resolved" : "open",
      );
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
    <PageFrame
      title="更多"
      description="产品反馈与后续次级入口集中在这里。"
      page="/more"
      workspace="more"
      onFeedbackCreated={() => setRefreshKey((value) => value + 1)}
    >
      <View className="workspace-stack">
        <View className="section-block">
          <View className="section-heading">
            <View>
              <Text className="section-title">产品反馈</Text>
              <Text className="section-subtitle">与 Desktop 共用同一套后端 Feedback 数据。</Text>
            </View>
            <Button className="button button--ghost button--compact" disabled={loading} onClick={() => void load()}>
              {loading ? "刷新中…" : "刷新"}
            </Button>
          </View>

          {error ? <Text className="inline-error">{error}</Text> : null}
          {loading && items.length === 0 ? <View className="page-state">正在读取反馈…</View> : null}
          {!loading && items.length === 0 ? (
            <View className="empty-state">还没有产品反馈。使用页面右上角“发送反馈”即可创建。</View>
          ) : null}

          <View className="feedback-list">
            {items.map((item) => (
              <View className="feedback-row" key={item.id}>
                <View className="feedback-row__body">
                  <View className="feedback-row__meta">
                    <Text className={`status-pill status-pill--${item.status}`}>{item.statusLabel}</Text>
                    <Text>{formatCreatedAt(item.createdAt)}</Text>
                  </View>
                  <Text className="feedback-row__content">{item.content}</Text>
                </View>
                <Button
                  className="button button--ghost"
                  disabled={pendingId === item.id}
                  onClick={() => void toggleStatus(item)}
                >
                  {pendingId === item.id
                    ? "保存中…"
                    : item.status === "open"
                      ? "标记已解决"
                      : "重新打开"}
                </Button>
              </View>
            ))}
          </View>
        </View>

        <View className="placeholder-block">
          <Text className="placeholder-block__title">自动化</Text>
          <Text className="placeholder-block__body">
            Scheduled Input 已有后端能力；Mini 自动化管理入口将在后续纵向迁移时接入。
          </Text>
        </View>
      </View>
    </PageFrame>
  );
}
