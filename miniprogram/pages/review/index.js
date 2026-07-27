const api = require("../../services/api.js");

Page({
  data: { reviews: [], pending: [], decided: [], categories: ["待分类", "餐饮", "食品杂货", "交通出行", "网购", "生活缴费", "快递物流", "汽车", "数码家电", "文娱", "其他"] },

  onShow() { this.load(); },

  load() {
    api.getReviews().then((reviews) => this.setData({
      reviews,
      pending: reviews.filter((item) => !item.decision),
      decided: reviews.filter((item) => item.decision)
    }));
  },

  onMerchantInput(event) {
    const id = event.currentTarget.dataset.id;
    const value = event.detail.value;
    this.setData({ pending: this.data.pending.map((item) => item.id === id ? Object.assign({}, item, { editMerchant: value }) : item) });
  },

  onCategoryChange(event) {
    const id = event.currentTarget.dataset.id;
    const category = this.data.categories[Number(event.detail.value)];
    this.setData({ pending: this.data.pending.map((item) => item.id === id ? Object.assign({}, item, { editCategory: category }) : item) });
  },

  confirm(event) {
    const item = this.data.pending.find((row) => row.id === event.currentTarget.dataset.id);
    const merchant = (item.editMerchant || item.suggestedMerchant || item.sourceMerchant).trim();
    const category = item.editCategory || item.suggestedCategory || "待分类";
    api.decideReview(item.id, { decision: "confirmed", merchant, category }).then(() => {
      wx.showToast({ title: "已确认", icon: "success" });
      this.load();
    });
  },

  ignore(event) {
    api.decideReview(event.currentTarget.dataset.id, { decision: "ignored" }).then(() => this.load());
  },

  resetAll() {
    wx.showModal({ title: "重置本地操作", content: "将清除所有本地确认和修改。", success: (result) => {
      if (result.confirm) api.resetLocalChanges().then(() => this.load());
    }});
  }
});
