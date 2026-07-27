const mock = require("../mock-data/billing.js");

const TX_OVERRIDES = "billing_tx_overrides_v1";
const REVIEW_DECISIONS = "billing_review_decisions_v1";

function getObject(key) {
  return wx.getStorageSync(key) || {};
}

function applyOverride(item) {
  const override = getObject(TX_OVERRIDES)[item.id] || {};
  return Object.assign({}, item, override);
}

function getMonths() {
  return Promise.resolve(mock.months);
}

function getSummary(month) {
  return Promise.resolve(mock.summaries[month] || {
    month, expense: 0, refund: 0, netExpense: 0, transactionCount: 0, pendingCount: 0
  });
}

function getTransactions(params) {
  const month = params.month;
  const keyword = (params.keyword || "").trim().toLowerCase();
  const filter = params.filter || "all";
  let rows = mock.transactions.filter((item) => item.month === month).map(applyOverride);
  if (keyword) {
    rows = rows.filter((item) =>
      `${item.merchant}${item.sourceMerchant}${item.category}`.toLowerCase().includes(keyword)
    );
  }
  if (filter === "pending") rows = rows.filter((item) => item.enrichmentStatus !== "mapping_applied");
  if (filter === "refund") rows = rows.filter((item) => item.direction === "refund");
  return Promise.resolve(rows);
}

function getTransaction(id) {
  const item = mock.transactions.find((row) => row.id === id);
  return Promise.resolve(item ? applyOverride(item) : null);
}

function updateTransaction(id, patch) {
  const all = getObject(TX_OVERRIDES);
  all[id] = Object.assign({}, all[id] || {}, patch, { locallyEdited: true });
  wx.setStorageSync(TX_OVERRIDES, all);
  return getTransaction(id);
}

function getReviews() {
  const decisions = getObject(REVIEW_DECISIONS);
  return Promise.resolve(mock.reviews.map((item) => Object.assign({}, item, decisions[item.id] || {})));
}

function decideReview(id, decision) {
  const all = getObject(REVIEW_DECISIONS);
  all[id] = Object.assign({}, decision, { decidedAt: Date.now() });
  wx.setStorageSync(REVIEW_DECISIONS, all);
  return Promise.resolve(all[id]);
}

function resetLocalChanges() {
  wx.removeStorageSync(TX_OVERRIDES);
  wx.removeStorageSync(REVIEW_DECISIONS);
  return Promise.resolve();
}

module.exports = {
  getMonths,
  getSummary,
  getTransactions,
  getTransaction,
  updateTransaction,
  getReviews,
  decideReview,
  resetLocalChanges
};
