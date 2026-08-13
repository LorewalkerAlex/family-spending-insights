import { Text, View } from "@tarojs/components";

import { PageFrame } from "../../components/PageFrame";

/** Review remains separate from product feedback and will absorb existing Mapping Review later. */
export default function ReviewPage() {
  return (
    <PageFrame
      title="审核"
      description="交易待分类、Mapping Review 与其他财务审核会统一进入这里。"
      page="/review"
      workspace="review"
    >
      <View className="placeholder-block">
        <Text className="placeholder-block__title">审核工作区迁移中</Text>
        <Text className="placeholder-block__body">
          产品反馈不属于财务审核；Feedback 已放在“更多”中独立管理。
        </Text>
      </View>
    </PageFrame>
  );
}
