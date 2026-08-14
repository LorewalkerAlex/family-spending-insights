import { Button, Text, View } from "@tarojs/components";
import Taro, { useDidShow } from "@tarojs/taro";
import { toTransactionListItemViewModel, type Transaction } from "@family-spending/core";
import { useCallback, useState } from "react";

import { familySpendingService } from "../../api/client";
import { PageFrame } from "../../components/PageFrame";
import "./index.css";

export default function TransactionsPage() {
  const [transactions, setTransactions] = useState<readonly Transaction[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const items = await familySpendingService.listTransactions();
      setTransactions(items.slice().sort((left, right) => right.date.localeCompare(left.date) || left.id.localeCompare(right.id)));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setLoading(false);
    }
  }, []);

  useDidShow(() => { void load(); });

  return (
    <PageFrame title="交易" description="浏览交易、来源与当前分类。" page="/transactions" workspace="transactions">
      <View className="transaction-mobile-actions">
        <Button className="button button--primary" onClick={() => void Taro.navigateTo({ url: "/pages/add-transaction/index" })}>添加交易</Button>
      </View>

      {loading && transactions.length === 0 ? <View className="page-state">正在读取交易…</View> : null}
      {error && transactions.length === 0 ? <View className="page-state page-state--error"><Text>{error}</Text><Button className="button button--ghost" onClick={() => void load()}>重试</Button></View> : null}
      {!loading && !error && transactions.length === 0 ? <View className="empty-state">暂无交易。</View> : null}

      <View className="transaction-mobile-list">
        {transactions.map((transaction) => {
          const item = toTransactionListItemViewModel(transaction);
          return (
            <View className="transaction-mobile-row" key={item.id} onClick={() => void Taro.navigateTo({ url: `/pages/transaction-detail/index?id=${encodeURIComponent(item.id)}` })}>
              <View className="transaction-mobile-row__main"><Text className="transaction-mobile-row__name">{item.displayName}</Text><Text className="transaction-mobile-row__meta">{item.date} · {item.typeLabel} · {item.sourceLabel}</Text></View>
              <View className="transaction-mobile-row__right"><Text className="transaction-mobile-row__amount">{item.amountText}</Text><Text className={`transaction-mobile-row__category${item.isUnclassified ? " transaction-mobile-row__category--attention" : ""}`}>{item.category}</Text></View>
            </View>
          );
        })}
      </View>
    </PageFrame>
  );
}
