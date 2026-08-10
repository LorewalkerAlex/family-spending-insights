"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  DEFAULT_API_BASE,
  createApplicationService,
  findSimilarMerchantNames,
  validateMappingReviewPreview,
  validateMappingReviewWorkspace,
} = require("./application-api.js");

function response(payload, options = {}) {
  return {
    ok: options.ok ?? true,
    status: options.status ?? 200,
    json: async () => structuredClone(payload),
  };
}

function workspacePayload() {
  return {
    items: [
      {
        description: "支付宝-待审核商户",
        transaction_count: 2,
        total_amount: "88.50",
        currency: "CNY",
        latest_date: "2026-08-09",
        source_types: ["cmb", "manual"],
        transaction_only_exception_count: 1,
      },
    ],
    merchants: [
      { name: "星巴克", default_category: "餐饮美食" },
      { name: "京东购物", default_category: "综合购物" },
    ],
    categories: ["餐饮美食", "综合购物"],
  };
}

function previewPayload(overrides = {}) {
  return {
    token: "a".repeat(64),
    description: "支付宝-待审核商户",
    merchant: "星巴克",
    category: "餐饮美食",
    is_new_merchant: false,
    previous_default_category: "餐饮美食",
    description_transaction_count: 2,
    description_affected_transaction_count: 1,
    default_category_affected_transaction_count: 0,
    total_affected_transaction_count: 1,
    preserved_merchant_exception_count: 1,
    preserved_category_exception_count: 0,
    ...overrides,
  };
}

test("validates Mapping Review workspace aggregation and Merchant options", () => {
  const workspace = validateMappingReviewWorkspace(workspacePayload());
  assert.equal(workspace.items[0].description, "支付宝-待审核商户");
  assert.equal(workspace.items[0].transactionCount, 2);
  assert.equal(workspace.items[0].transactionOnlyExceptionCount, 1);
  assert.equal(workspace.merchants[0].name, "星巴克");
  assert.equal(workspace.merchants[0].defaultCategory, "餐饮美食");
  assert.ok(Object.isFrozen(workspace));
  assert.ok(Object.isFrozen(workspace.items));
});

test("Merchant similarity hints are lightweight and never merge names", () => {
  const merchants = workspacePayload().merchants;
  assert.deepEqual(findSimilarMerchantNames("星 巴 克", merchants), ["星巴克"]);
  assert.deepEqual(findSimilarMerchantNames("京东", merchants), ["京东购物"]);
  assert.deepEqual(findSimilarMerchantNames("完全不同", merchants), []);
});

test("loads Mapping Review workspace from the Application API", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({ mapping_review: workspacePayload() });
    },
  });

  const workspace = await service.getMappingReviews();
  assert.equal(workspace.items.length, 1);
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/mapping-reviews`);
  assert.equal(requests[0].options.method, "GET");
});

test("previews Mapping Review impact before mutation", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({ preview: previewPayload() });
    },
  });

  const preview = await service.previewMappingReview({
    description: "支付宝-待审核商户",
    merchant: "星巴克",
    category: "餐饮美食",
  });
  assert.equal(preview.totalAffectedTransactionCount, 1);
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/mapping-reviews/preview`);
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    description: "支付宝-待审核商户",
    merchant: "星巴克",
    category: "餐饮美食",
  });
});

test("apply sends the preview token and explicit new-Merchant confirmation", async () => {
  const requests = [];
  const service = createApplicationService({
    fetchImpl: async (url, options) => {
      requests.push({ url, options });
      return response({ mapping_review: previewPayload({ is_new_merchant: true, previous_default_category: null }) });
    },
  });

  const applied = await service.applyMappingReview({
    description: "支付宝-待审核商户",
    merchant: "新商户",
    category: "餐饮美食",
    previewToken: "a".repeat(64),
    confirmNewMerchant: true,
  });
  assert.equal(applied.isNewMerchant, true);
  assert.equal(requests[0].url, `${DEFAULT_API_BASE}/mapping-reviews/apply`);
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].options.body), {
    description: "支付宝-待审核商户",
    merchant: "新商户",
    category: "餐饮美食",
    preview_token: "a".repeat(64),
    confirm_new_merchant: true,
  });
});

test("rejects malformed preview tokens from successful responses", () => {
  assert.throws(() => validateMappingReviewPreview(previewPayload({ token: "stale" })));
});