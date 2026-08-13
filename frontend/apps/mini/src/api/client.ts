import Taro from "@tarojs/taro";
import { FamilySpendingService, type FeedbackRuntime } from "@family-spending/core";

import { TaroTransport } from "./taro-transport";

declare const FAMILY_SPENDING_API_BASE_URL: string;

const taroEnvironment = Taro.getEnv();
const isWeChatMiniProgram = taroEnvironment === Taro.ENV_TYPE.WEAPP;
const apiBaseUrl = isWeChatMiniProgram ? FAMILY_SPENDING_API_BASE_URL.trim() : "";

export const miniRuntime: FeedbackRuntime = isWeChatMiniProgram ? "weapp" : "mini_h5";

export const familySpendingService = new FamilySpendingService(
  new TaroTransport(apiBaseUrl, {
    requireAbsoluteBaseUrl: isWeChatMiniProgram,
  }),
);
