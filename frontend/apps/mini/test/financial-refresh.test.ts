import { describe, expect, it } from "vitest";

import {
  currentFinancialDataVersion,
  hasFinancialDataChanged,
  markFinancialDataChanged,
} from "../miniprogram/state/financial-refresh";

describe("Mini financial refresh invalidation", () => {
  it("marks page snapshots stale without storing financial data", () => {
    const before = currentFinancialDataVersion();
    expect(hasFinancialDataChanged(before)).toBe(false);

    const after = markFinancialDataChanged();
    expect(after).toBe(before + 1);
    expect(hasFinancialDataChanged(before)).toBe(true);
    expect(hasFinancialDataChanged(after)).toBe(false);
  });
});
