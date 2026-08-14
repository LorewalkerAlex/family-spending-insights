import { describe, expect, it, vi } from "vitest";

import { BrowserTransport } from "../src/api/browser-transport";
import { workspaceForPath, workspaceNavigation } from "../src/app/workspaces";

describe("BrowserTransport", () => {
  it("keeps the shared service contract on JSON fetch without leaking browser APIs into core", async () => {
    const calls: Array<{ url: string; init: RequestInit | undefined }> = [];
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init });
      return new Response(JSON.stringify({ feedback: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    };

    const transport = new BrowserTransport("http://127.0.0.1:8765/", fetchImpl);
    const response = await transport.request({
      method: "POST",
      path: "/api/feedback",
      body: { content: "test" },
    });

    expect(response).toEqual({ status: 200, body: { feedback: [] } });
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("http://127.0.0.1:8765/api/feedback");
    expect(calls[0]?.init).toMatchObject({
      method: "POST",
      body: JSON.stringify({ content: "test" }),
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
    });
  });

  it("binds the default browser fetch to globalThis", async () => {
    let fetchThis: unknown;
    const browserFetch = function (this: unknown) {
      fetchThis = this;
      return Promise.resolve(
        new Response(JSON.stringify({ feedback: [] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    } as typeof fetch;

    vi.stubGlobal("fetch", browserFetch);
    try {
      const transport = new BrowserTransport();
      await transport.request({ method: "GET", path: "/api/feedback" });
      expect(fetchThis).toBe(globalThis);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("preserves non-JSON error bodies for ApiResponseError diagnostics", async () => {
    const transport = new BrowserTransport("", async () =>
      new Response("proxy unavailable", { status: 502 }),
    );

    await expect(
      transport.request({ method: "GET", path: "/api/financial-summary" }),
    ).resolves.toEqual({ status: 502, body: "proxy unavailable" });
  });
});

describe("workspace navigation", () => {
  it("keeps the canonical five workspaces and marks migrated surfaces as implemented", () => {
    expect(workspaceNavigation.map((item) => item.id)).toEqual([
      "overview",
      "transactions",
      "review",
      "automation",
      "feedback",
    ]);
    expect(workspaceNavigation.filter((item) => item.implemented).map((item) => item.id)).toEqual([
      "overview",
      "transactions",
      "review",
      "automation",
      "feedback",
    ]);
  });

  it("maps only canonical route paths into Feedback workspace context", () => {
    expect(workspaceForPath("/feedback/")).toBe("feedback");
    expect(workspaceForPath("/not-a-workspace")).toBeUndefined();
  });
});
