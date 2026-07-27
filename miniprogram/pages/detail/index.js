const api = require("../../services/api.js");

Page({
  data: { item: null, merchant: "", category: "", categories: ["待分类", "餐饮", "食品杂货", "交通出行", "网购", "生活缴费", "快递物流", "汽车", "数码家电", "文娱", "其他"] },

  onLoad(options) {
    this.id = options.id;
    api.getTransaction(this.id).then((item) => {
      if (!item) return wx.showToast({ title: "流水不存在", icon: "none" });
      this.setData({ item, merchant: item.merchant, category: item.category });
    });
  },

  onMerchantInput(event) { this.setData({ merchant: event.detail.value }); },
  onCategoryChange(event) { this.setData({ category: this.data.categories[Number(event.detail.value)] }); },

  save() {
    const merchant = this.data.merchant.trim();
    if (!merchant) return wx.showToast({ title: "请填写商户名称", icon: "none" });
    api.updateTransaction(this.id, { merchant, category: this.data.category }).then((item) => {
      this.setData({ item });
      wx.showToast({ title: "已保存到本地", icon: "success" });
    });
  }
});
