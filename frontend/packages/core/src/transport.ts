export type HttpMethod = "GET" | "POST" | "PATCH" | "DELETE";

export interface HttpRequest {
  method: HttpMethod;
  path: string;
  body?: unknown;
}

export interface HttpResponse {
  status: number;
  body: unknown;
}

export interface HttpTransport {
  request(request: HttpRequest): Promise<HttpResponse>;
}

export class ApiResponseError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(message: string, status: number, body: unknown) {
    super(message);
    this.name = "ApiResponseError";
    this.status = status;
    this.body = body;
  }
}

export function requireHttpStatus(response: HttpResponse, expectedStatus: number): unknown {
  if (response.status !== expectedStatus) {
    const backendMessage =
      response.body &&
      typeof response.body === "object" &&
      "error" in response.body &&
      typeof (response.body as { error?: unknown }).error === "string"
        ? (response.body as { error: string }).error.trim()
        : "";
    throw new ApiResponseError(
      backendMessage ||
        `Unexpected API response status: expected ${expectedStatus}, received ${response.status}`,
      response.status,
      response.body,
    );
  }
  return response.body;
}
