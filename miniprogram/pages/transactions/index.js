const api = require("../../services/api.js");

Page({
  data: {
    months: [],
    month: "2026-06",
    monthLabel: "2026年6月",
    summary: {},
    groups: [],
    keyword: "",
    filter: "all",
    filters: [
      { key: "all", label: "全部" },
      { key: "pending", label: "待整理" },
      { key: "refund", label: "退款" }
    ],
    loading: true
  },

  onLoad() {
    api.getMonths().then((months) => {
      const month = months[0] || "2026-06";
      this.setData({ months, month, monthLabel: this.formatMonth(month) });
      this.loadData();
    });
  },

  onShow() {
    if (this.data.months.length) this.loadData();
  },

  onPullDownRefresh() {
    this.loadData().finally(() => wx.stopPullDownRefresh());
  },

  formatMonth(month) {
    const parts = month.split("-");
    return `${parts[0]}年${Number(parts[1])}月`;
  },

  loadData() {
    this.setData({ loading: true });
    return Promise.all([
      api.getSummary(this.data.month),
      api.getTransactions({ month: this.data.month, keyword: this.data.keyword, filter: this.data.filter })
    ]).then(([summary, rows]) => {
      const byDate = {};
      rows = rows.map((item) => Object.assign({}, item, { avatar: (item.merchant || "?").slice(0, 1) }));
      rows.forEach((item) => {
        if (!byDate[item.transactionDate]) byDate[item.transactionDate] = [];
        byDate[item.transactionDate].push(item);
      });
      const groups = Object.keys(byDate).sort().reverse().map((date) => ({
        date,
        label: `${Number(date.slice(5, 7))}月${Number(date.slice(8, 10))}日`,
        items: byDate[date]
      }));
      this.setData({ summary, groups, loading: false });
    });
  },

  onKeywordInput(event) {
    this.setData({ keyword: event.detail.value });
  },

  onSearch() { this.loadData(); },

  onFilterTap(event) {
    this.setData({ filter: event.currentTarget.dataset.key });
    this.loadData();
  },

  onMonthChange(event) {
    const month = this.data.months[Number(event.detail.value)];
    this.setData({ month, monthLabel: this.formatMonth(month) });
    this.loadData();
  },

  openDetail(event) {
    wx.navigateTo({ url: `/pages/detail/index?id=${event.currentTarget.dataset.id}` });
  }
});
