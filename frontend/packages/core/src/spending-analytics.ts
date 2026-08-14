import { z } from "zod";

import { formatCnyMinorUnits } from "./presentation";

const safeIntegerSchema = z
  .number()
  .int()
  .min(Number.MIN_SAFE_INTEGER)
  .max(Number.MAX_SAFE_INTEGER);
const nonNegativeSafeIntegerSchema = safeIntegerSchema.min(0);
const monthNameSchema = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/);
const nonEmptyTextSchema = z.string().trim().min(1);

function addSafeInteger(current: number, value: number): number | null {
  const next = current + value;
  return Number.isSafeInteger(next) ? next : null;
}

function reconcileRows(
  rows: readonly { spending_minor: number; transaction_count: number }[],
  expectedSpending: number,
  expectedCount: number,
  context: z.RefinementCtx,
  path: readonly (string | number)[],
  label: string,
): void {
  let spending = 0;
  let count = 0;
  for (const row of rows) {
    const nextSpending = addSafeInteger(spending, row.spending_minor);
    const nextCount = addSafeInteger(count, row.transaction_count);
    if (nextSpending === null || nextCount === null) {
      context.addIssue({
        code: "custom",
        message: `${label} totals exceed the safe integer range`,
        path: [...path],
      });
      return;
    }
    spending = nextSpending;
    count = nextCount;
  }
  if (spending !== expectedSpending || count !== expectedCount) {
    context.addIssue({
      code: "custom",
      message: `${label} rows do not reconcile with the month total`,
      path: [...path],
    });
  }
}

const spendingCategorySchema = z
  .object({
    category: nonEmptyTextSchema,
    spending_minor: nonNegativeSafeIntegerSchema,
    transaction_count: nonNegativeSafeIntegerSchema,
  })
  .strict();

const spendingMerchantSchema = z
  .object({
    merchant_name: nonEmptyTextSchema.nullable(),
    display_name: nonEmptyTextSchema,
    is_unclassified: z.boolean(),
    spending_minor: nonNegativeSafeIntegerSchema,
    transaction_count: nonNegativeSafeIntegerSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (value.is_unclassified !== (value.merchant_name === null)) {
      context.addIssue({
        code: "custom",
        message: "Merchant is_unclassified must match missing merchant_name",
        path: ["is_unclassified"],
      });
    }
  });

const spendingMonthSchema = z
  .object({
    month: monthNameSchema,
    is_complete: z.boolean(),
    show: z.boolean(),
    total_spending_minor: nonNegativeSafeIntegerSchema,
    transaction_count: nonNegativeSafeIntegerSchema,
    categories: z.array(spendingCategorySchema),
    merchants: z.array(spendingMerchantSchema),
  })
  .strict()
  .superRefine((value, context) => {
    const categoryNames = new Set<string>();
    value.categories.forEach((item, index) => {
      if (categoryNames.has(item.category)) {
        context.addIssue({
          code: "custom",
          message: `Duplicate spending category: ${item.category}`,
          path: ["categories", index, "category"],
        });
      }
      categoryNames.add(item.category);
    });

    const merchantKeys = new Set<string>();
    value.merchants.forEach((item, index) => {
      const key = `${item.merchant_name ?? ""}\u0000${item.display_name}\u0000${item.is_unclassified}`;
      if (merchantKeys.has(key)) {
        context.addIssue({
          code: "custom",
          message: `Duplicate spending merchant row: ${item.display_name}`,
          path: ["merchants", index, "display_name"],
        });
      }
      merchantKeys.add(key);
    });

    reconcileRows(
      value.categories,
      value.total_spending_minor,
      value.transaction_count,
      context,
      ["categories"],
      "Category",
    );
    reconcileRows(
      value.merchants,
      value.total_spending_minor,
      value.transaction_count,
      context,
      ["merchants"],
      "Merchant",
    );
  });

const spendingAggregateSchema = z
  .object({
    total_spending_minor: nonNegativeSafeIntegerSchema,
    transaction_count: nonNegativeSafeIntegerSchema,
    month_count: nonNegativeSafeIntegerSchema,
  })
  .strict();

function reconcileAggregate(
  aggregate: z.infer<typeof spendingAggregateSchema>,
  months: readonly z.infer<typeof spendingMonthSchema>[],
  context: z.RefinementCtx,
  path: readonly (string | number)[],
): void {
  let spending = 0;
  let count = 0;
  for (const month of months) {
    const nextSpending = addSafeInteger(spending, month.total_spending_minor);
    const nextCount = addSafeInteger(count, month.transaction_count);
    if (nextSpending === null || nextCount === null) {
      context.addIssue({
        code: "custom",
        message: "Spending aggregate exceeds the safe integer range",
        path: [...path],
      });
      return;
    }
    spending = nextSpending;
    count = nextCount;
  }
  if (
    aggregate.month_count !== months.length ||
    aggregate.total_spending_minor !== spending ||
    aggregate.transaction_count !== count
  ) {
    context.addIssue({
      code: "custom",
      message: "Spending aggregate does not reconcile with its month rows",
      path: [...path],
    });
  }
}

export const spendingStatisticsSchema = z
  .object({
    schema_version: z.literal(2),
    summary: z
      .object({
        all_data: spendingAggregateSchema,
        shown_data: spendingAggregateSchema,
      })
      .strict(),
    months: z.array(spendingMonthSchema),
  })
  .strict()
  .superRefine((value, context) => {
    const seenMonths = new Set<string>();
    value.months.forEach((month, index) => {
      if (seenMonths.has(month.month)) {
        context.addIssue({
          code: "custom",
          message: `Duplicate Spending Statistics month: ${month.month}`,
          path: ["months", index, "month"],
        });
      }
      seenMonths.add(month.month);
    });

    reconcileAggregate(value.summary.all_data, value.months, context, ["summary", "all_data"]);
    reconcileAggregate(
      value.summary.shown_data,
      value.months.filter((month) => month.show),
      context,
      ["summary", "shown_data"],
    );
  });

export const spendingStatisticsResponseSchema = z
  .object({ spending_statistics: spendingStatisticsSchema })
  .strict();

export type SpendingStatistics = z.infer<typeof spendingStatisticsSchema>;
export type SpendingStatisticsMonth = z.infer<typeof spendingMonthSchema>;

export interface SpendingMonthOptionViewModel {
  month: string;
  totalSpendingText: string;
  transactionCount: number;
}

export interface SpendingCategoryViewModel {
  rank: number;
  category: string;
  spendingMinor: number;
  spendingText: string;
  transactionCount: number;
  sharePercent: number;
  shareText: string;
  isUnclassified: boolean;
}

export interface SpendingMerchantViewModel {
  rank: number;
  displayName: string;
  spendingMinor: number;
  spendingText: string;
  transactionCount: number;
  sharePercent: number;
  shareText: string;
  isUnclassified: boolean;
}

export interface SpendingAnalyticsViewModel {
  monthOptions: readonly SpendingMonthOptionViewModel[];
  selectedMonth: string | null;
  isComplete: boolean;
  totalSpendingMinor: number;
  totalSpendingText: string;
  transactionCount: number;
  categories: readonly SpendingCategoryViewModel[];
  topMerchants: readonly SpendingMerchantViewModel[];
}

function sharePercent(value: number, total: number): number {
  if (total <= 0 || value <= 0) return 0;
  return Math.round((value / total) * 1000) / 10;
}

export function toSpendingAnalyticsViewModel(
  statistics: SpendingStatistics,
  selectedMonth?: string,
  merchantLimit = 8,
): SpendingAnalyticsViewModel {
  if (!Number.isInteger(merchantLimit) || merchantLimit <= 0) {
    throw new TypeError("Spending merchant limit must be a positive integer");
  }

  const shownMonths = statistics.months
    .filter((month) => month.show)
    .slice()
    .sort((left, right) => right.month.localeCompare(left.month));
  const selected =
    shownMonths.find((month) => month.month === selectedMonth) ?? shownMonths[0] ?? null;

  const monthOptions = shownMonths.map((month) => ({
    month: month.month,
    totalSpendingText: formatCnyMinorUnits(month.total_spending_minor),
    transactionCount: month.transaction_count,
  }));

  if (selected === null) {
    return {
      monthOptions,
      selectedMonth: null,
      isComplete: false,
      totalSpendingMinor: 0,
      totalSpendingText: formatCnyMinorUnits(0),
      transactionCount: 0,
      categories: [],
      topMerchants: [],
    };
  }

  const total = selected.total_spending_minor;
  return {
    monthOptions,
    selectedMonth: selected.month,
    isComplete: selected.is_complete,
    totalSpendingMinor: total,
    totalSpendingText: formatCnyMinorUnits(total),
    transactionCount: selected.transaction_count,
    categories: selected.categories.map((item, index) => {
      const share = sharePercent(item.spending_minor, total);
      return {
        rank: index + 1,
        category: item.category,
        spendingMinor: item.spending_minor,
        spendingText: formatCnyMinorUnits(item.spending_minor),
        transactionCount: item.transaction_count,
        sharePercent: share,
        shareText: `${share.toFixed(1)}%`,
        isUnclassified: item.category === "待分类",
      };
    }),
    topMerchants: selected.merchants.slice(0, merchantLimit).map((item, index) => {
      const share = sharePercent(item.spending_minor, total);
      return {
        rank: index + 1,
        displayName: item.display_name,
        spendingMinor: item.spending_minor,
        spendingText: formatCnyMinorUnits(item.spending_minor),
        transactionCount: item.transaction_count,
        sharePercent: share,
        shareText: `${share.toFixed(1)}%`,
        isUnclassified: item.is_unclassified,
      };
    }),
  };
}
