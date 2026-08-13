import Taro from "@tarojs/taro";
import type { HttpRequest, HttpResponse, HttpTransport } from "@family-spending/core";

export type TaroRequester = (
  options: Parameters<typeof Taro.request>[0],
) => ReturnType<typeof Taro.request>;

interface TaroTransportOptions {
  requireAbsoluteBaseUrl?: boolean;
  requester?: TaroRequester;
}

function normalizeBaseUrl(value: string): string {
  return value.trim().replace(/\/$/, "");
}

/** Taro transport owns Mini networking while preserving the shared HTTP method/path contract unchanged. */
export class TaroTransport implements HttpTransport {
  private readonly baseUrl: string;
  private readonly requireAbsoluteBaseUrl: boolean;
  private readonly requester: TaroRequester;

  constructor(apiBaseUrl = "", options: TaroTransportOptions = {}) {
    this.baseUrl = normalizeBaseUrl(apiBaseUrl);
    this.requireAbsoluteBaseUrl = options.requireAbsoluteBaseUrl ?? false;
    this.requester = options.requester ?? (Taro.request as TaroRequester);
  }

  async request(request: HttpRequest): Promise<HttpResponse> {
    if (this.requireAbsoluteBaseUrl && !/^https:\/\//.test(this.baseUrl)) {
      throw new Error(
        "WeChat Mini Program runtime requires TARO_APP_API_BASE_URL to be an absolute HTTPS origin",
      );
    }

    const options: Parameters<typeof Taro.request>[0] = {
      url: `${this.baseUrl}${request.path}`,
      method: request.method,
      header: {
        "content-type": "application/json",
      },
      dataType: "json",
    };
    if (request.body !== undefined) {
      options.data = request.body;
    }

    const response = await this.requester(options);
    return {
      status: response.statusCode,
      body: response.data,
    };
  }
}
