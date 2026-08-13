import {
  createFeedbackCommandSchema,
  feedbackItemResponseSchema,
  feedbackListResponseSchema,
  financialSummaryResponseSchema,
  updateFeedbackStatusCommandSchema,
  type CreateFeedbackCommand,
  type FeedbackItem,
  type FeedbackStatus,
  type FinancialSummary,
} from "./contracts";
import { requireHttpStatus, type HttpTransport } from "./transport";

export class FamilySpendingService {
  constructor(private readonly transport: HttpTransport) {}

  async getFinancialSummary(): Promise<FinancialSummary> {
    const response = await this.transport.request({
      method: "GET",
      path: "/api/financial-summary",
    });
    const payload = financialSummaryResponseSchema.parse(requireHttpStatus(response, 200));
    return payload.financial_summary;
  }

  async listFeedback(): Promise<readonly FeedbackItem[]> {
    const response = await this.transport.request({
      method: "GET",
      path: "/api/feedback",
    });
    return feedbackListResponseSchema.parse(requireHttpStatus(response, 200)).feedback;
  }

  async createFeedback(command: CreateFeedbackCommand): Promise<FeedbackItem> {
    const body = createFeedbackCommandSchema.parse(command);
    const response = await this.transport.request({
      method: "POST",
      path: "/api/feedback",
      body,
    });
    return feedbackItemResponseSchema.parse(requireHttpStatus(response, 201)).feedback;
  }

  async updateFeedbackStatus(id: string, status: FeedbackStatus): Promise<FeedbackItem> {
    const feedbackId = id.trim();
    if (!feedbackId) {
      throw new TypeError("Feedback id must be a non-empty string");
    }
    const body = updateFeedbackStatusCommandSchema.parse({ status });
    const response = await this.transport.request({
      method: "PATCH",
      path: `/api/feedback/${encodeURIComponent(feedbackId)}`,
      body,
    });
    return feedbackItemResponseSchema.parse(requireHttpStatus(response, 200)).feedback;
  }
}
