export default defineAppConfig({
  pages: [
    "pages/overview/index",
    "pages/transactions/index",
    "pages/review/index",
    "pages/more/index",
    "pages/add-transaction/index",
    "pages/transaction-detail/index",
    "pages/review-detail/index",
  ],
  window: {
    backgroundTextStyle: "light",
    navigationBarBackgroundColor: "#f7f8f6",
    navigationBarTitleText: "家庭消费",
    navigationBarTextStyle: "black",
    backgroundColor: "#f7f8f6",
  },
  tabBar: {
    color: "#687169",
    selectedColor: "#2f6b4f",
    backgroundColor: "#ffffff",
    borderStyle: "white",
    list: [
      { pagePath: "pages/overview/index", text: "总览" },
      { pagePath: "pages/transactions/index", text: "交易" },
      { pagePath: "pages/review/index", text: "审核" },
      { pagePath: "pages/more/index", text: "更多" },
    ],
  },
});
