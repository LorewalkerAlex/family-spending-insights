import type { HttpRequest, HttpResponse, HttpTransport } from "@family-spending/core";

/** Browser-only HTTP transport; domain services remain independent of fetch and the DOM. */
export class BrowserTransport implements HttpTransport {
  private readonly baseUrl: string;
  private readonly fetchImpl: typeof fetch;

  constructor(baseUrl = "", fetchImpl?: typeof fetch) {
    const resolvedFetch =
      fetchImpl ??
      (typeof globalThis.fetch === "function" ? globalThis.fetch.bind(globalThis) : undefined);

    if (typeof resolvedFetch !== "function") {
      throw new TypeError("BrowserTransport requires a fetch implementation");
    }

    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.fetchImpl = resolvedFetch;
  }

  async request(request: HttpRequest): Promise<HttpResponse> {
    const init: RequestInit = {
      method: request.method,
      headers: { Accept: "application/json" },
    };

    if (request.body !== undefined) {
      init.headers = {
        Accept: "application/json",
        "Content-Type": "application/json",
      };
      init.body = JSON.stringify(request.body);
    }

    const response = await this.fetchImpl(`${this.baseUrl}${request.path}`, init);
    const text = await response.text();
    let body: unknown = null;

    if (text) {
      try {
        body = JSON.parse(text) as unknown;
      } catch {
        body = text;
      }
    }

    return { status: response.status, body };
  }
}
