import { describe, expect, it } from "vitest";

import {
  applyMiniThemeChrome,
  miniThemeOptions,
  persistMiniTheme,
  readMiniTheme,
} from "../miniprogram/theme";

describe("native Mini dopamine themes", () => {
  it("keeps the same four tone identities as Desktop", () => {
    expect(miniThemeOptions.map((item) => [item.id, item.label])).toEqual([
      ["lime", "酸柠"],
      ["berry", "莓果"],
      ["tangerine", "橘浪"],
      ["grape", "葡萄"],
    ]);
  });

  it("reads a persisted theme and falls back to lime for unknown values", () => {
    expect(
      readMiniTheme({
        getStorageSync: () => "grape",
      }),
    ).toBe("grape");

    expect(
      readMiniTheme({
        getStorageSync: () => "unknown",
      }),
    ).toBe("lime");
  });

  it("persists the selected theme using the Mini storage contract", () => {
    const writes: Array<[string, unknown]> = [];

    persistMiniTheme("berry", {
      setStorageSync(key, value) {
        writes.push([key, value]);
      },
    });

    expect(writes).toEqual([["family-spending.mini-theme", "berry"]]);
  });

  it("applies the selected tone to native navigation and tab chrome", () => {
    const calls: Array<[string, unknown]> = [];

    applyMiniThemeChrome("tangerine", {
      setNavigationBarColor(options) {
        calls.push(["navigation", options]);
        return undefined;
      },
      setTabBarStyle(options) {
        calls.push(["tab", options]);
        return undefined;
      },
    });

    expect(calls).toEqual([
      [
        "navigation",
        {
          frontColor: "#000000",
          backgroundColor: "#fff9ee",
        },
      ],
      [
        "tab",
        {
          color: "#7c6b62",
          selectedColor: "#ff5e37",
          backgroundColor: "#ffffff",
          borderStyle: "white",
        },
      ],
    ]);
  });
});
