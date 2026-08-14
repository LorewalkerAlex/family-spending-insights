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
  type Transaction,
} from "./contracts";
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
}
