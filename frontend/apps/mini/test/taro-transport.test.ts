import { describe, expect, it, vi } from "vitest";

vi.mock("@tarojs/taro", () => ({
  default: {
    request: vi.fn(),
  },
}));

import { TaroTransport, type TaroRequester } from "../src/api/taro-transport";

function requesterReturning(statusCode: number, data: unknown) {
  const calls: Parameters<TaroRequester>[0][] = [];
  const requester = vi.fn(async (options: Parameters<TaroRequester>[0]) => {
    calls.push(options);
    return {
      statusCode,
      data,
      header: {},
      cookies: [],
      errMsg: "request:ok",
    } as Awaited<ReturnType<TaroRequester>>;
  }) as unknown as TaroRequester;
  return { calls, requester };
}

describe("TaroTransport", () => {
  it("uses relative requests for H5-style same-origin transport", async () => {
    const { calls, requester } = requesterReturning(200, { ok: true });
    const transport = new TaroTransport("", { requester });

    await expect(transport.request({ method: "GET", path: "/api/financial-summary" })).resolves.toEqual({
      status: 200,
      body: { ok: true },
    });

    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/financial-summary");
    expect(calls[0]?.method).toBe("GET");
  });

  it("preserves the shared PATCH contract for feedback status updates", async () => {
    const { calls, requester } = requesterReturning(200, { feedback: { status: "resolved" } });
    const transport = new TaroTransport("https://api.example.test/", { requester });

    await transport.request({
      method: "PATCH",
      path: "/api/feedback/feedback_1",
      body: { status: "resolved" },
    });

    expect(calls[0]?.url).toBe("https://api.example.test/api/feedback/feedback_1");
    expect(calls[0]?.method).toBe("PATCH");
    expect(calls[0]?.header).toMatchObject({
      "content-type": "application/json",
    });
    expect(calls[0]?.data).toEqual({ status: "resolved" });
  });

  it("requires an HTTPS API origin when configured for WeChat runtime", async () => {
    const { requester } = requesterReturning(200, {});
    const transport = new TaroTransport("http://127.0.0.1:8765", {
      requester,
      requireAbsoluteBaseUrl: true,
    });

    await expect(
      transport.request({ method: "GET", path: "/api/financial-summary" }),
    ).rejects.toThrow("absolute HTTPS origin");
  });
});
