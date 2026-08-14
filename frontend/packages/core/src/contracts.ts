import { z } from "zod";

const safeIntegerSchema = z
  .number()
  .int()
  .min(Number.MIN_SAFE_INTEGER)
  .max(Number.MAX_SAFE_INTEGER);

const nonNegativeSafeIntegerSchema = safeIntegerSchema.min(0);
const positiveSafeIntegerSchema = safeIntegerSchema.min(1);
const monthNameSchema = z.string().regex(/^\d{4}-(0[1-9]|1[0-2])$/);
const isoDateSchema = z.string().regex(/^\d{4}-\d{2}-\d{2}$/);
const decimalAmountSchema = z.string().trim().regex(/^-?\d+(?:\.\d+)?$/);
const nonEmptyTextSchema = z.string().trim().min(1);
const nullableTextSchema = nonEmptyTextSchema.nullable();
const sha256TokenSchema = z.string().regex(/^[0-9a-f]{64}$/);

const financialAggregateSchema = z
  .object({
    total_income_minor: nonNegativeSafeIntegerSchema,
    total_spending_minor: nonNegativeSafeIntegerSchema,
    net_cash_flow_minor: safeIntegerSchema,
    income_transaction_count: nonNegativeSafeIntegerSchema,
    spending_transaction_count: nonNegativeSafeIntegerSchema,
    month_count: nonNegativeSafeIntegerSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (value.net_cash_flow_minor !== value.total_income_minor - value.total_spending_minor) {
      context.addIssue({
        code: "custom",
        message: "net_cash_flow_minor must equal income minus net spending",
        path: ["net_cash_flow_minor"],
      });
    }
  });

const financialMonthSchema = z
  .object({
    month: monthNameSchema,
    spending_data_complete: z.boolean(),
    show: z.boolean(),
    total_income_minor: nonNegativeSafeIntegerSchema,
    income_transaction_count: nonNegativeSafeIntegerSchema,
    total_spending_minor: nonNegativeSafeIntegerSchema,
    spending_transaction_count: nonNegativeSafeIntegerSchema,
    net_cash_flow_minor: safeIntegerSchema,
  })
  .strict()
  .superRefine((value, context) => {
    if (value.net_cash_flow_minor !== value.total_income_minor - value.total_spending_minor) {
      context.addIssue({
        code: "custom",
        message: "net_cash_flow_minor must equal income minus net spending",
        path: ["net_cash_flow_minor"],
      });
    }
  });

interface FinancialAggregateTotals {
  income: number;
  spending: number;
  incomeCount: number;
  spendingCount: number;
}

function addSafeInteger(current: number, value: number): number | null {
  const next = current + value;
  return Number.isSafeInteger(next) ? next : null;
}

function reconcileAggregate(
  aggregate: z.infer<typeof financialAggregateSchema>,
  months: readonly z.infer<typeof financialMonthSchema>[],
  context: z.RefinementCtx,
  path: readonly (string | number)[],
): void {
  let totals: FinancialAggregateTotals = {
    income: 0,
    spending: 0,
    incomeCount: 0,
    spendingCount: 0,
  };

  for (const month of months) {
    const income = addSafeInteger(totals.income, month.total_income_minor);
    const spending = addSafeInteger(totals.spending, month.total_spending_minor);
    const incomeCount = addSafeInteger(totals.incomeCount, month.income_transaction_count);
    const spendingCount = addSafeInteger(totals.spendingCount, month.spending_transaction_count);

    if (income === null || spending === null || incomeCount === null || spendingCount === null) {
      context.addIssue({
        code: "custom",
        message: "Financial Summary month totals exceed the safe integer range",
        path: [...path],
      });
      return;
    }

    totals = { income, spending, incomeCount, spendingCount };
  }

  const matches =
    aggregate.month_count === months.length &&
    aggregate.total_income_minor === totals.income &&
    aggregate.total_spending_minor === totals.spending &&
    aggregate.income_transaction_count === totals.incomeCount &&
    aggregate.spending_transaction_count === totals.spendingCount;

  if (!matches) {
    context.addIssue({
      code: "custom",
      message: "Financial Summary aggregate does not reconcile with its month rows",
      path: [...path],
    });
  }
}

export const financialSummarySchema = z
  .object({
    schema_version: z.literal(1),
    summary: z
      .object({
        all_data: financialAggregateSchema,
        shown_data: financialAggregateSchema,
      })
      .strict(),
    months: z.array(financialMonthSchema),
  })
  .strict()
  .superRefine((value, context) => {
    const seen = new Set<string>();
    value.months.forEach((month, index) => {
      if (seen.has(month.month)) {
        context.addIssue({
          code: "custom",
          message: `Duplicate Financial Summary month: ${month.month}`,
          path: ["months", index, "month"],
        });
      }
      seen.add(month.month);
    });

    reconcileAggregate(value.summary.all_data, value.months, context, ["summary", "all_data"]);
    reconcileAggregate(
      value.summary.shown_data,
      value.months.filter((month) => month.show),
      context,
      ["summary", "shown_data"],
    );
  });

export const financialSummaryResponseSchema = z
  .object({
    financial_summary: financialSummarySchema,
  })
  .strict();

export const feedbackStatusSchema = z.enum(["open", "resolved"]);
export const feedbackRuntimeSchema = z.enum(["desktop_web", "mini_h5", "weapp"]);

export const feedbackContextSchema = z
  .object({
    runtime: feedbackRuntimeSchema.optional(),
    page: z.string().trim().min(1).optional(),
    workspace: z.string().trim().min(1).optional(),
    entity_type: z.string().trim().min(1).optional(),
    entity_id: z.string().trim().min(1).optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if ((value.entity_type === undefined) !== (value.entity_id === undefined)) {
      context.addIssue({
        code: "custom",
        message: "entity_type and entity_id must be provided together",
        path: ["entity_type"],
      });
    }
  });

const utcTimestampSchema = z.string().refine(
  (value) => value.endsWith("Z") && !Number.isNaN(Date.parse(value)),
  "created_at must be a valid UTC ISO timestamp",
);

export const feedbackItemSchema = z
  .object({
    id: z.string().trim().min(1),
    created_at: utcTimestampSchema,
    status: feedbackStatusSchema,
    content: z.string().trim().min(1),
    context: feedbackContextSchema,
  })
  .strict();

export const feedbackListResponseSchema = z
  .object({ feedback: z.array(feedbackItemSchema) })
  .strict();

export const feedbackItemResponseSchema = z
  .object({ feedback: feedbackItemSchema })
  .strict();

export const createFeedbackCommandSchema = z
  .object({
    content: z.string().trim().min(1),
    context: feedbackContextSchema.optional(),
  })
  .strict();

export const updateFeedbackStatusCommandSchema = z
  .object({ status: feedbackStatusSchema })
  .strict();

export const transactionTypeSchema = z.enum(["income", "expense"]);
export const categorySourceSchema = z.enum([
  "merchant_default",
  "transaction_override",
  "manual_override",
  "income_default",
  "unclassified",
]);
export const manualInputActionSchema = z.enum(["created", "matched", "reused"]);
export const sourceRoleSchema = z.enum(["authoritative", "supporting"]);

export const transactionSchema = z
  .object({
    id: nonEmptyTextSchema,
    type: transactionTypeSchema,
    date: isoDateSchema,
    amount: decimalAmountSchema,
    currency: nonEmptyTextSchema,
    source: z
      .object({
        id: nonEmptyTextSchema,
        type: nonEmptyTextSchema,
        description: nullableTextSchema,
      })
      .strict(),
    enrichment: z
      .object({
        merchant: nullableTextSchema,
        display_name: nonEmptyTextSchema,
        default_category: nullableTextSchema,
        category: nonEmptyTextSchema,
        category_source: categorySourceSchema,
        note: nullableTextSchema,
        is_unclassified: z.boolean(),
        review_signals: z.array(nonEmptyTextSchema),
      })
      .strict(),
  })
  .strict();

export const transactionListResponseSchema = z
  .object({ transactions: z.array(transactionSchema) })
  .strict();
export const transactionResponseSchema = z
  .object({ transaction: transactionSchema })
  .strict();
export const categoriesResponseSchema = z
  .object({ categories: z.array(nonEmptyTextSchema) })
  .strict();
export const manualDescriptionsResponseSchema = z
  .object({ descriptions: z.array(nonEmptyTextSchema) })
  .strict();

export const manualInputResultSchema = z
  .object({
    source_record_id: nonEmptyTextSchema,
    action: manualInputActionSchema,
    transaction: transactionSchema,
  })
  .strict();

export const manualInputRecordSchema = z
  .object({
    source_record_id: nonEmptyTextSchema,
    transaction_id: nonEmptyTextSchema,
    source_role: sourceRoleSchema,
    type: transactionTypeSchema,
    date: isoDateSchema,
    amount: decimalAmountSchema,
    currency: nonEmptyTextSchema,
    description: nullableTextSchema,
    note: nullableTextSchema,
    transaction: transactionSchema,
  })
  .strict();

export const manualInputListResponseSchema = z
  .object({ manual_inputs: z.array(manualInputRecordSchema) })
  .strict();
export const manualInputResultResponseSchema = z
  .object({ manual_input: manualInputResultSchema })
  .strict();

export const manualInputCorrectionSchema = z
  .object({
    replaced_source_record_id: nonEmptyTextSchema,
    manual_input: manualInputResultSchema,
  })
  .strict();
export const manualInputCorrectionResponseSchema = z
  .object({ manual_input_correction: manualInputCorrectionSchema })
  .strict();

export const manualInputDeletionSchema = z
  .object({
    source_record_id: nonEmptyTextSchema,
    transaction_id: nonEmptyTextSchema,
    transaction_removed: z.boolean(),
  })
  .strict();
export const manualInputDeletionResponseSchema = z
  .object({ manual_input_deletion: manualInputDeletionSchema })
  .strict();

export const manualInputCommandSchema = z
  .object({
    type: transactionTypeSchema,
    date: isoDateSchema,
    amount: decimalAmountSchema,
    description: nonEmptyTextSchema,
    note: nullableTextSchema.optional(),
  })
  .strict();

export const enrichmentPatchSchema = z
  .object({
    merchant: nullableTextSchema.optional(),
    category: nullableTextSchema.optional(),
    note: nullableTextSchema.optional(),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.merchant === undefined && value.category === undefined && value.note === undefined) {
      context.addIssue({
        code: "custom",
        message: "Enrichment patch requires at least one field",
      });
    }
  });

export const mappingReviewItemSchema = z
  .object({
    description: nonEmptyTextSchema,
    transaction_count: positiveSafeIntegerSchema,
    total_amount: decimalAmountSchema,
    currency: nonEmptyTextSchema,
    latest_date: isoDateSchema,
    source_types: z.array(nonEmptyTextSchema).min(1),
    transaction_only_exception_count: nonNegativeSafeIntegerSchema,
  })
  .strict();

export const merchantMappingOptionSchema = z
  .object({
    name: nonEmptyTextSchema,
    default_category: nonEmptyTextSchema,
  })
  .strict();

export const mappingReviewWorkspaceSchema = z
  .object({
    items: z.array(mappingReviewItemSchema),
    merchants: z.array(merchantMappingOptionSchema),
    categories: z.array(nonEmptyTextSchema),
  })
  .strict();

export const mappingReviewPreviewSchema = z
  .object({
    token: sha256TokenSchema,
    description: nonEmptyTextSchema,
    merchant: nonEmptyTextSchema,
    category: nonEmptyTextSchema,
    is_new_merchant: z.boolean(),
    previous_default_category: nullableTextSchema,
    description_transaction_count: positiveSafeIntegerSchema,
    description_affected_transaction_count: nonNegativeSafeIntegerSchema,
    default_category_affected_transaction_count: nonNegativeSafeIntegerSchema,
    total_affected_transaction_count: nonNegativeSafeIntegerSchema,
    preserved_merchant_exception_count: nonNegativeSafeIntegerSchema,
    preserved_category_exception_count: nonNegativeSafeIntegerSchema,
  })
  .strict();

export const mappingReviewWorkspaceResponseSchema = z
  .object({ mapping_review: mappingReviewWorkspaceSchema })
  .strict();
export const mappingReviewPreviewResponseSchema = z
  .object({ preview: mappingReviewPreviewSchema })
  .strict();
export const mappingReviewApplyResponseSchema = z
  .object({ mapping_review: mappingReviewPreviewSchema })
  .strict();

export const mappingReviewCommandSchema = z
  .object({
    description: nonEmptyTextSchema,
    merchant: nonEmptyTextSchema,
    category: nonEmptyTextSchema,
  })
  .strict();

export const mappingReviewApplyCommandSchema = mappingReviewCommandSchema
  .extend({
    previewToken: sha256TokenSchema,
    confirmNewMerchant: z.boolean().optional(),
  })
  .strict();



export const scheduledInputActionSchema = z.enum(["created", "matched", "reused", "recovered"]);
const scheduledDateSchema = isoDateSchema.refine(
  (value) => {
    const day = Number(value.slice(8, 10));
    return day >= 1 && day <= 28;
  },
  "Scheduled Input V1 only supports monthly occurrence days 1-28",
);

export const scheduledInputRuleSchema = z
  .object({
    id: nonEmptyTextSchema,
    enabled: z.boolean(),
    type: transactionTypeSchema,
    amount: decimalAmountSchema,
    currency: nonEmptyTextSchema,
    description: nonEmptyTextSchema,
    note: nullableTextSchema,
    next_date: scheduledDateSchema,
    last_occurrence_date: scheduledDateSchema.nullable(),
    last_source_record_id: nullableTextSchema,
    last_transaction_id: nullableTextSchema,
    last_action: scheduledInputActionSchema.nullable(),
  })
  .strict()
  .superRefine((value, context) => {
    const lastValues = [
      value.last_occurrence_date,
      value.last_source_record_id,
      value.last_transaction_id,
      value.last_action,
    ];
    const presentCount = lastValues.filter((item) => item !== null).length;
    if (presentCount !== 0 && presentCount !== lastValues.length) {
      context.addIssue({
        code: "custom",
        message: "Scheduled Input last execution metadata must be all present or all absent",
      });
    }
    if (value.last_occurrence_date !== null && value.next_date <= value.last_occurrence_date) {
      context.addIssue({
        code: "custom",
        message: "Scheduled Input next_date must be after the last generated occurrence",
        path: ["next_date"],
      });
    }
  });

export const scheduledInputListResponseSchema = z
  .object({ scheduled_inputs: z.array(scheduledInputRuleSchema) })
  .strict();
export const scheduledInputResponseSchema = z
  .object({ scheduled_input: scheduledInputRuleSchema })
  .strict();

export const scheduledInputCommandSchema = z
  .object({
    type: transactionTypeSchema,
    amount: decimalAmountSchema,
    description: nonEmptyTextSchema,
    note: nullableTextSchema.optional(),
    nextDate: scheduledDateSchema,
    enabled: z.boolean(),
  })
  .strict();

export const scheduledInputOccurrenceSchema = z
  .object({
    rule_id: nonEmptyTextSchema,
    occurrence_date: scheduledDateSchema,
    source_record_id: nonEmptyTextSchema,
    transaction_id: nonEmptyTextSchema,
    action: scheduledInputActionSchema,
  })
  .strict();

export const scheduledInputRunSchema = z
  .object({
    generated_count: nonNegativeSafeIntegerSchema,
    occurrences: z.array(scheduledInputOccurrenceSchema),
  })
  .strict()
  .superRefine((value, context) => {
    if (value.generated_count !== value.occurrences.length) {
      context.addIssue({
        code: "custom",
        message: "Scheduled Input generated_count must equal occurrences length",
        path: ["generated_count"],
      });
    }
  });
export const scheduledInputRunResponseSchema = z
  .object({ scheduled_input_run: scheduledInputRunSchema })
  .strict();
export const scheduledInputDeletionResponseSchema = z
  .object({ scheduled_input_deletion: z.object({ id: nonEmptyTextSchema }).strict() })
  .strict();

export type FinancialSummary = z.infer<typeof financialSummarySchema>;
export type FinancialMonth = z.infer<typeof financialMonthSchema>;
export type FeedbackStatus = z.infer<typeof feedbackStatusSchema>;
export type FeedbackRuntime = z.infer<typeof feedbackRuntimeSchema>;
export type FeedbackContext = z.infer<typeof feedbackContextSchema>;
export type FeedbackItem = z.infer<typeof feedbackItemSchema>;
export type CreateFeedbackCommand = z.input<typeof createFeedbackCommandSchema>;
export type TransactionType = z.infer<typeof transactionTypeSchema>;
export type Transaction = z.infer<typeof transactionSchema>;
export type CategorySource = z.infer<typeof categorySourceSchema>;
export type ManualInputAction = z.infer<typeof manualInputActionSchema>;
export type ManualInputResult = z.infer<typeof manualInputResultSchema>;
export type ManualInputRecord = z.infer<typeof manualInputRecordSchema>;
export type ManualInputCorrection = z.infer<typeof manualInputCorrectionSchema>;
export type ManualInputDeletion = z.infer<typeof manualInputDeletionSchema>;
export type ManualInputCommand = z.input<typeof manualInputCommandSchema>;
export type EnrichmentPatch = z.input<typeof enrichmentPatchSchema>;
export type MappingReviewItem = z.infer<typeof mappingReviewItemSchema>;
export type MerchantMappingOption = z.infer<typeof merchantMappingOptionSchema>;
export type MappingReviewWorkspace = z.infer<typeof mappingReviewWorkspaceSchema>;
export type MappingReviewPreview = z.infer<typeof mappingReviewPreviewSchema>;
export type MappingReviewCommand = z.input<typeof mappingReviewCommandSchema>;
export type MappingReviewApplyCommand = z.input<typeof mappingReviewApplyCommandSchema>;
export type ScheduledInputAction = z.infer<typeof scheduledInputActionSchema>;
export type ScheduledInputRule = z.infer<typeof scheduledInputRuleSchema>;
export type ScheduledInputCommand = z.input<typeof scheduledInputCommandSchema>;
export type ScheduledInputOccurrence = z.infer<typeof scheduledInputOccurrenceSchema>;
export type ScheduledInputRun = z.infer<typeof scheduledInputRunSchema>;
