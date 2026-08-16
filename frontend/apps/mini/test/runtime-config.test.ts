import { describe, expect, it } from "vitest";

import { resolveApiBaseUrl } from "../miniprogram/config/runtime";

describe("native Mini runtime config", () => {
  it("uses the Canonical local API in Developer Tools", () => {
    expect(resolveApiBaseUrl("develop")).toBe("http://127.0.0.1:8765");
  });

  it("fails closed for trial/release until HTTPS deployment is configured", () => {
    expect(() => resolveApiBaseUrl("trial")).toThrow("HTTPS API");
    expect(() => resolveApiBaseUrl("release")).toThrow("HTTPS API");
  });
});
