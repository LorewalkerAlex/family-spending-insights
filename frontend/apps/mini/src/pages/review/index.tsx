import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import {
  toMappingReviewListItemViewModel,
  type MappingReviewWorkspace,
} from "@family-spending/core";
import { useCallback, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";
import "./index.css";

export default function ReviewPage() {
  const [workspace, setWorkspace] = useState<MappingReviewWorkspace | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setWorkspace(await familySpendingService.getMappingReviewWorkspace());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useDidShow(() => { void load(); });

  return (
    <PageFrame
      title="审核"
      description="处理待分类 Expense 的 Merchant Mapping。"
      page="/review"
      workspace="review"
    >
      <View className="review-mobile-summary">
        <Text><Text className="review-mobile-summary__count">{workspace?.items.length ?? 0}</Text> 个待审核 description</Text>
        <Button className="button button--ghost button--compact" disabled={loading} onClick={() => void load()}>
          {loading ? "刷新中…" : "刷新"}
        </Button>
      </View>

      {loading && !workspace ? <View className="page-state">正在读取审核队列…</View> : null}
      {error && !workspace ? (
        <View className="page-state page-state--error">
          <Text>{error}</Text>
          <Button className="button button--ghost" onClick={() => void load()}>重试</Button>
        </View>
      ) : null}
      {workspace && workspace.items.length === 0 ? (
        <View className="review-mobile-empty">
          <Text className="review-mobile-empty__title">没有待分类 Expense</Text>
          <Text className="review-mobile-hint">Income 不进入 Merchant Mapping，因此不会出现在审核队列。</Text>
        </View>
      ) : null}

      <View className="review-mobile-list">
        {workspace?.items.map((item) => {
          const view = toMappingReviewListItemViewModel(item);
          return (
            <View
              className="review-mobile-row"
              key={item.description}
              onClick={() => void Taro.navigateTo({
                url: `/pages/review-detail/index?description=${encodeURIComponent(item.description)}`,
              })}
            >
              <View className="review-mobile-row__main">
                <Text className="review-mobile-row__name">{view.description}</Text>
                <Text className="review-mobile-row__meta">{view.latestDate} · {view.sourceTypesText}</Text>
              </View>
              <View className="review-mobile-row__right">
                <Text className="review-mobile-row__amount">{view.amountText}</Text>
                <Text className="review-mobile-row__meta">
                  {view.transactionCountText}
                  {view.transactionOnlyExceptionCount > 0 ? ` · ${view.transactionOnlyExceptionCount} 笔单笔例外` : ""}
                </Text>
              </View>
            </View>
          );
        })}
      </View>
    </PageFrame>
  );
}
