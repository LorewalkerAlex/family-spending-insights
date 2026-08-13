import { Text, View } from "@tarojs/components";

import { PageFrame } from "../../components/PageFrame";

/** Placeholder is explicit so the POC never suggests transaction migration is already complete. */
export default function TransactionsPage() {
  return (
    <PageFrame
      title="交易"
      description="现有交易能力仍由 local_dashboard 提供，本页将在后续迁移。"
      page="/transactions"
      workspace="transactions"
    >
      <View className="placeholder-block">
        <Text className="placeholder-block__title">交易工作区迁移中</Text>
        <Text className="placeholder-block__body">
          本轮只验证跨平台壳层、Financial Summary 与 Feedback，不复制旧 Dashboard 的交易逻辑。
        </Text>
      </View>
    </PageFrame>
  );
}
