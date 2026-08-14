import {
  categoriesResponseSchema,
  createFeedbackCommandSchema,
  enrichmentPatchSchema,
  feedbackItemResponseSchema,
  feedbackListResponseSchema,
  financialSummaryResponseSchema,
  manualDescriptionsResponseSchema,
  manualInputCommandSchema,
  manualInputCorrectionResponseSchema,
  manualInputDeletionResponseSchema,
  manualInputListResponseSchema,
  manualInputResultResponseSchema,
  mappingReviewApplyCommandSchema,
  mappingReviewApplyResponseSchema,
  mappingReviewCommandSchema,
  mappingReviewPreviewResponseSchema,
  mappingReviewWorkspaceResponseSchema,
  scheduledInputCommandSchema,
  scheduledInputDeletionResponseSchema,
  scheduledInputListResponseSchema,
  scheduledInputResponseSchema,
  scheduledInputRunResponseSchema,
  transactionListResponseSchema,
  transactionResponseSchema,
  updateFeedbackStatusCommandSchema,
  type CreateFeedbackCommand,
  type EnrichmentPatch,
  type FeedbackItem,
  type FeedbackStatus,
  type FinancialSummary,
  type ManualInputCommand,
  type ManualInputCorrection,
  type ManualInputDeletion,
  type ManualInputRecord,
  type ManualInputResult,
  type MappingReviewApplyCommand,
  type MappingReviewCommand,
  type MappingReviewPreview,
  type MappingReviewWorkspace,
  type ScheduledInputCommand,
  type ScheduledInputRule,
  type ScheduledInputRun,
  type Transaction,
} from "./contracts";
import {
  spendingStatisticsResponseSchema,
  type SpendingStatistics,
} from "./spending-analytics";
import { requireHttpStatus, type HttpTransport } from "./transport";

function requireId(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) {
    throw new TypeError(`${label} must be a non-empty string`);
  }
  return normalized;
}

export class FamilySpendingService {
  constructor(private readonly transport: HttpTransport) {}

  async getFinancialSummary(): Promise<FinancialSummary> {
    const response = await this.transport.request({ method: "GET", path: "/api/financial-summary" });
    return financialSummaryResponseSchema.parse(requireHttpStatus(response, 200)).financial_summary;
  }

  async getSpendingStatistics(): Promise<SpendingStatistics> {
    const response = await this.transport.request({ method: "GET", path: "/api/spending-statistics" });
    return spendingStatisticsResponseSchema.parse(requireHttpStatus(response, 200)).spending_statistics;
  }

  async listFeedback(): Promise<readonly FeedbackItem[]> {
    const response = await this.transport.request({ method: "GET", path: "/api/feedback" });
    return feedbackListResponseSchema.parse(requireHttpStatus(response, 200)).feedback;
  }

  async createFeedback(command: CreateFeedbackCommand): Promise<FeedbackItem> {
    const body = createFeedbackCommandSchema.parse(command);
    const response = await this.transport.request({ method: "POST", path: "/api/feedback", body });
    return feedbackItemResponseSchema.parse(requireHttpStatus(response, 201)).feedback;
  }

  async updateFeedbackStatus(id: string, status: FeedbackStatus): Promise<FeedbackItem> {
    const feedbackId = requireId(id, "Feedback id");
    const body = updateFeedbackStatusCommandSchema.parse({ status });
    const response = await this.transport.request({
      method: "PATCH",
      path: `/api/feedback/${encodeURIComponent(feedbackId)}`,
      body,
    });
    return feedbackItemResponseSchema.parse(requireHttpStatus(response, 200)).feedback;
  }

  async listCategories(): Promise<readonly string[]> {
    const response = await this.transport.request({ method: "GET", path: "/api/categories" });
    return categoriesResponseSchema.parse(requireHttpStatus(response, 200)).categories;
  }

  async listTransactions(): Promise<readonly Transaction[]> {
    const response = await this.transport.request({ method: "GET", path: "/api/transactions" });
    return transactionListResponseSchema.parse(requireHttpStatus(response, 200)).transactions;
  }

  async getTransaction(id: string): Promise<Transaction> {
    const transactionId = requireId(id, "Transaction id");
    const response = await this.transport.request({
      method: "GET",
      path: `/api/transactions/${encodeURIComponent(transactionId)}`,
    });
    return transactionResponseSchema.parse(requireHttpStatus(response, 200)).transaction;
  }

  async listManualDescriptions(): Promise<readonly string[]> {
    const response = await this.transport.request({
      method: "GET",
      path: "/api/manual-descriptions",
    });
    return manualDescriptionsResponseSchema.parse(requireHttpStatus(response, 200)).descriptions;
  }

  async listManualInputs(): Promise<readonly ManualInputRecord[]> {
    const response = await this.transport.request({ method: "GET", path: "/api/manual-inputs" });
    return manualInputListResponseSchema.parse(requireHttpStatus(response, 200)).manual_inputs;
  }

  async createManualInput(command: ManualInputCommand): Promise<ManualInputResult> {
    const body = manualInputCommandSchema.parse(command);
    const response = await this.transport.request({ method: "POST", path: "/api/manual-inputs", body });
    return manualInputResultResponseSchema.parse(requireHttpStatus(response, 201)).manual_input;
  }

  async correctManualInput(
    sourceRecordId: string,
    command: ManualInputCommand,
  ): Promise<ManualInputCorrection> {
    const id = requireId(sourceRecordId, "Manual source record id");
    const body = manualInputCommandSchema.parse(command);
    const response = await this.transport.request({
      method: "POST",
      path: `/api/manual-inputs/${encodeURIComponent(id)}/corrections`,
      body,
    });
    return manualInputCorrectionResponseSchema.parse(requireHttpStatus(response, 200))
      .manual_input_correction;
  }

  async deleteManualInput(sourceRecordId: string): Promise<ManualInputDeletion> {
    const id = requireId(sourceRecordId, "Manual source record id");
    const response = await this.transport.request({
      method: "DELETE",
      path: `/api/manual-inputs/${encodeURIComponent(id)}`,
    });
    return manualInputDeletionResponseSchema.parse(requireHttpStatus(response, 200))
      .manual_input_deletion;
  }

  async updateEnrichment(transactionId: string, patch: EnrichmentPatch): Promise<Transaction> {
    const id = requireId(transactionId, "Transaction id");
    const body = enrichmentPatchSchema.parse(patch);
    const response = await this.transport.request({
      method: "PATCH",
      path: `/api/transactions/${encodeURIComponent(id)}/enrichment`,
      body,
    });
    return transactionResponseSchema.parse(requireHttpStatus(response, 200)).transaction;
  }

  async getMappingReviewWorkspace(): Promise<MappingReviewWorkspace> {
    const response = await this.transport.request({ method: "GET", path: "/api/mapping-reviews" });
    return mappingReviewWorkspaceResponseSchema.parse(requireHttpStatus(response, 200)).mapping_review;
  }

  async previewMappingReview(command: MappingReviewCommand): Promise<MappingReviewPreview> {
    const body = mappingReviewCommandSchema.parse(command);
    const response = await this.transport.request({
      method: "POST",
      path: "/api/mapping-reviews/preview",
      body,
    });
    return mappingReviewPreviewResponseSchema.parse(requireHttpStatus(response, 200)).preview;
  }

  async applyMappingReview(command: MappingReviewApplyCommand): Promise<MappingReviewPreview> {
    const input = mappingReviewApplyCommandSchema.parse(command);
    const body = {
      description: input.description,
      merchant: input.merchant,
      category: input.category,
      preview_token: input.previewToken,
      confirm_new_merchant: input.confirmNewMerchant ?? false,
    };
    const response = await this.transport.request({
      method: "POST",
      path: "/api/mapping-reviews/apply",
      body,
    });
    return mappingReviewApplyResponseSchema.parse(requireHttpStatus(response, 200)).mapping_review;
  }

  async listScheduledInputs(): Promise<readonly ScheduledInputRule[]> {
    const response = await this.transport.request({ method: "GET", path: "/api/scheduled-inputs" });
    return scheduledInputListResponseSchema.parse(requireHttpStatus(response, 200)).scheduled_inputs;
  }

  async createScheduledInput(command: ScheduledInputCommand): Promise<ScheduledInputRule> {
    const input = scheduledInputCommandSchema.parse(command);
    const body = {
      type: input.type,
      amount: input.amount,
      description: input.description,
      note: input.note ?? null,
      next_date: input.nextDate,
      enabled: input.enabled,
    };
    const response = await this.transport.request({ method: "POST", path: "/api/scheduled-inputs", body });
    return scheduledInputResponseSchema.parse(requireHttpStatus(response, 201)).scheduled_input;
  }

  async updateScheduledInput(id: string, command: ScheduledInputCommand): Promise<ScheduledInputRule> {
    const ruleId = requireId(id, "Scheduled Input id");
    const input = scheduledInputCommandSchema.parse(command);
    const body = {
      type: input.type,
      amount: input.amount,
      description: input.description,
      note: input.note ?? null,
      next_date: input.nextDate,
      enabled: input.enabled,
    };
    const response = await this.transport.request({
      method: "PATCH",
      path: `/api/scheduled-inputs/${encodeURIComponent(ruleId)}`,
      body,
    });
    return scheduledInputResponseSchema.parse(requireHttpStatus(response, 200)).scheduled_input;
  }

  async deleteScheduledInput(id: string): Promise<string> {
    const ruleId = requireId(id, "Scheduled Input id");
    const response = await this.transport.request({
      method: "DELETE",
      path: `/api/scheduled-inputs/${encodeURIComponent(ruleId)}`,
    });
    return scheduledInputDeletionResponseSchema.parse(requireHttpStatus(response, 200)).scheduled_input_deletion.id;
  }

  async runDueScheduledInputs(): Promise<ScheduledInputRun> {
    const response = await this.transport.request({ method: "POST", path: "/api/scheduled-inputs/run-due" });
    return scheduledInputRunResponseSchema.parse(requireHttpStatus(response, 200)).scheduled_input_run;
  }
}
