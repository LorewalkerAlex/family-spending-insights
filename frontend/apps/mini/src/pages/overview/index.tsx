import { Button, Text, View } from "@tarojs/components";
import {
  toFinancialSummaryViewModel,
  type FinancialSummaryViewModel,
} from "@family-spending/core";
import { useCallback, useEffect, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";

/** Mini Overview mirrors Desktop financial semantics while using a touch-first compact layout. */
export default function OverviewPage() {
  const [summary, setSummary] = useState<FinancialSummaryViewModel | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const payload = await familySpendingService.getFinancialSummary();
      setSummary(toFinancialSummaryViewModel(payload));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <PageFrame
      title="家庭总览"
      description="先看净现金流，再看近期完整月份。"
      page="/overview"
      workspace="overview"
    >
      {loading && summary === null ? <View className="page-state">正在读取家庭财务摘要…</View> : null}

      {error && summary === null ? (
        <View className="page-state page-state--error">
          <Text>财务摘要加载失败：{error}</Text>
          <Button className="button button--ghost" onClick={() => void load()}>
            重新加载
          </Button>
        </View>
      ) : null}

      {summary ? (
        <View className="workspace-stack">
          <View className="financial-hero">
            <Text className="eyebrow">展示期净现金流</Text>
            <Text
              className={`financial-hero__amount ${summary.hero.netCashFlowMinor < 0 ? "financial-hero__amount--negative" : ""}`.trim()}
            >
              {summary.hero.netCashFlowText}
            </Text>
            <Text className="financial-hero__note">
              后端当前展示 {summary.hero.monthCount} 个完整消费月份；收入来源覆盖不在这里推断。
            </Text>
            <View className="financial-hero__breakdown">
              <View>
                <Text className="metric-label">收入</Text>
                <Text className="metric-value">{summary.hero.totalIncomeText}</Text>
              </View>
              <View>
                <Text className="metric-label">净消费</Text>
                <Text className="metric-value">{summary.hero.totalSpendingText}</Text>
              </View>
            </View>
          </View>

          <View className="section-block">
            <View className="section-heading">
              <View>
                <Text className="section-title">近期月份</Text>
                <Text className="section-subtitle">仅展示后端 show=true 的月份。</Text>
              </View>
              {error ? (
                <Button className="button button--ghost button--compact" onClick={() => void load()}>
                  重试
                </Button>
              ) : null}
            </View>

            {summary.visibleMonths.length === 0 ? (
              <View className="empty-state">暂无可展示的完整月份。</View>
            ) : (
              <View className="month-list">
                {summary.visibleMonths.slice(0, 4).map((month) => (
                  <View className="month-row" key={month.month}>
                    <View>
                      <Text className="month-row__name">{month.month}</Text>
                      <Text className="month-row__secondary">消费 {month.totalSpendingText}</Text>
                    </View>
                    <View className="month-row__right">
                      <Text className="month-row__cash-flow">{month.netCashFlowText}</Text>
                      <Text className="month-row__secondary">收入 {month.totalIncomeText}</Text>
                    </View>
                  </View>
                ))}
              </View>
            )}
          </View>
        </View>
      ) : null}
    </PageFrame>
  );
}
