import { describe, expect, it, vi } from "vitest";

import {
  createFamilySpendingApi,
  type RequestOptions,
  type Requester,
} from "../miniprogram/services/api";

function requesterFor(
  responses: Record<string, { statusCode: number; data: unknown }>,
): { calls: RequestOptions[]; requester: Requester } {
  const calls: RequestOptions[] = [];
  const requester: Requester = (options) => {
    calls.push(options);
    const response = responses[options.url];
    if (!response) {
      options.fail({ errMsg: "request:fail missing fixture" });
      return undefined;
    }
    options.success(response);
    return undefined;
  };
  return { calls, requester: vi.fn(requester) };
}

function previewPayload(overrides: Record<string, unknown> = {}) {
  return {
    token: "a".repeat(64),
    description: "needs review",
    merchant: "Known Merchant",
    category: "餐饮美食",
    is_new_merchant: false,
    previous_default_category: "餐饮美食",
    description_transaction_count: 3,
    description_affected_transaction_count: 3,
    default_category_affected_transaction_count: 0,
    total_affected_transaction_count: 3,
    preserved_merchant_exception_count: 1,
    preserved_category_exception_count: 0,
    ...overrides,
  };
}

describe("native Mini Mapping Review API", () => {
  it("previews one Mapping Review through the canonical preview route", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { calls, requester } = requesterFor({
      [`${baseUrl}/api/mapping-reviews/preview`]: {
        statusCode: 200,
        data: { preview: previewPayload() },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(
      api.previewMappingReview({
        description: "needs review",
        merchant: "Known Merchant",
        category: "餐饮美食",
      }),
    ).resolves.toMatchObject({
      token: "a".repeat(64),
      total_affected_transaction_count: 3,
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({
      method: "POST",
      url: `${baseUrl}/api/mapping-reviews/preview`,
      data: {
        description: "needs review",
        merchant: "Known Merchant",
        category: "餐饮美食",
      },
    });
  });

  it("applies the exact preview token and explicit new-Merchant confirmation", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const token = "b".repeat(64);
    const { calls, requester } = requesterFor({
      [`${baseUrl}/api/mapping-reviews/apply`]: {
        statusCode: 200,
        data: {
          mapping_review: previewPayload({
            token,
            merchant: "New Merchant",
            is_new_merchant: true,
            previous_default_category: null,
          }),
        },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(
      api.applyMappingReview({
        description: "needs review",
        merchant: "New Merchant",
        category: "餐饮美食",
        preview_token: token,
        confirm_new_merchant: true,
      }),
    ).resolves.toMatchObject({
      merchant: "New Merchant",
      is_new_merchant: true,
    });

    expect(calls[0]).toMatchObject({
      method: "POST",
      data: {
        description: "needs review",
        merchant: "New Merchant",
        category: "餐饮美食",
        preview_token: token,
        confirm_new_merchant: true,
      },
    });
  });

  it("rejects malformed preview tokens before the page trusts the response", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { requester } = requesterFor({
      [`${baseUrl}/api/mapping-reviews/preview`]: {
        statusCode: 200,
        data: { preview: previewPayload({ token: "not-a-token" }) },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(
      api.previewMappingReview({
        description: "needs review",
        merchant: "Known Merchant",
        category: "餐饮美食",
      }),
    ).rejects.toThrow("SHA-256 token");
  });

  it("surfaces stale-preview conflicts with Backend detail", async () => {
    const baseUrl = "http://127.0.0.1:8765";
    const { requester } = requesterFor({
      [`${baseUrl}/api/mapping-reviews/apply`]: {
        statusCode: 409,
        data: { error: "Mapping Review state changed after preview; refresh before applying" },
      },
    });
    const api = createFamilySpendingApi({ baseUrl, requester });

    await expect(
      api.applyMappingReview({
        description: "needs review",
        merchant: "Known Merchant",
        category: "餐饮美食",
        preview_token: "c".repeat(64),
        confirm_new_merchant: false,
      }),
    ).rejects.toThrow("state changed after preview");
  });
});