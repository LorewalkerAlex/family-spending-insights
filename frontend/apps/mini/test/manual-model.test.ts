import { describe, expect, it } from "vitest";

import {
  buildDescriptionAssist,
  findSimilarManualDescriptions,
  manualInputActionLabel,
  mergeManualDescription,
  normalizeManualDescription,
  todayIsoDate,
  validateManualDraft,
} from "../miniprogram/pages/manual/model";

describe("Manual Input presentation model", () => {
  it("formats the local calendar date without UTC conversion", () => {
    expect(todayIsoDate(new Date(2026, 7, 16, 23, 30))).toBe("2026-08-16");
  });

  it("uses only whitespace-insensitive exact/prefix matching for description suggestions", () => {
    const descriptions = ["盒马超市", "盒 马", "盒马鲜生", "星巴克", "盒马配送"];

    expect(normalizeManualDescription(" 盒 马 ")).toBe("盒马");
    expect(findSimilarManualDescriptions("盒马", descriptions)).toEqual([
      "盒 马",
      "盒马超市",
      "盒马鲜生",
      "盒马配送",
    ]);
    expect(findSimilarManualDescriptions("完全不同", descriptions)).toEqual([]);
  });

  it("requires explicit confirmation before creating normalized-duplicate text", () => {
    const descriptions = ["小区门口早餐摊"];
    const assist = buildDescriptionAssist("小区 门口 早餐摊", descriptions);

    expect(assist).toMatchObject({
      normalizedDuplicate: "小区门口早餐摊",
      hasExactExisting: false,
      hasNormalizedDuplicate: true,
    });

    const blocked = validateManualDraft(
      {
        type: "expense",
        date: "2026-08-16",
        amount: "18.50",
        description: "小区 门口 早餐摊",
        note: "",
      },
      descriptions,
      "",
    );
    expect(blocked).toMatchObject({ ok: false });

    const confirmed = validateManualDraft(
      {
        type: "expense",
        date: "2026-08-16",
        amount: "18.50",
        description: "小区 门口 早餐摊",
        note: "  周末早餐  ",
      },
      descriptions,
      "小区 门口 早餐摊",
    );
    expect(confirmed).toEqual({
      ok: true,
      command: {
        type: "expense",
        date: "2026-08-16",
        amount: "18.50",
        description: "小区 门口 早餐摊",
        note: "周末早餐",
      },
    });
  });

  it("allows negative expense adjustments but keeps income strictly positive", () => {
    expect(
      validateManualDraft(
        {
          type: "expense",
          date: "2026-08-16",
          amount: "-12.50",
          description: "退款",
          note: "",
        },
        [],
        "",
      ),
    ).toMatchObject({ ok: true });

    expect(
      validateManualDraft(
        {
          type: "income",
          date: "2026-08-16",
          amount: "0",
          description: "工资",
          note: "",
        },
        [],
        "",
      ),
    ).toEqual({ ok: false, message: "收入金额必须大于 0。" });
  });

  it("keeps exact existing text reusable and exposes Backend reconciliation labels", () => {
    expect(buildDescriptionAssist("咖啡", ["咖啡"])).toMatchObject({
      hasExactExisting: true,
      hasNormalizedDuplicate: false,
    });
    expect(manualInputActionLabel("created")).toBe("已创建新交易");
    expect(manualInputActionLabel("matched")).toBe("已匹配已有交易");
    expect(manualInputActionLabel("reused")).toBe("已保留既有交易");
  });

  it("adds successful new descriptions only once", () => {
    expect(mergeManualDescription(["早餐"], "咖啡")).toEqual(["早餐", "咖啡"]);
    expect(mergeManualDescription(["早餐", "咖啡"], "咖啡")).toEqual(["早餐", "咖啡"]);
  });
});
