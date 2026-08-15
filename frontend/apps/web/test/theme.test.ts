import { describe, expect, it } from "vitest";

import {
  applyDesktopTheme,
  desktopThemeOptions,
  persistDesktopTheme,
  readDesktopTheme,
} from "../src/theme";

describe("desktop dopamine themes", () => {
  it("exposes four saturated product palettes", () => {
    expect(desktopThemeOptions.map((option) => option.id)).toEqual([
      "lime",
      "berry",
      "tangerine",
      "grape",
    ]);
    expect(new Set(desktopThemeOptions.map((option) => option.label)).size).toBe(4);
  });

  it("falls back to Lime when persisted state is unavailable or obsolete", () => {
    expect(readDesktopTheme(null)).toBe("lime");
    expect(readDesktopTheme({ getItem: () => "forest" })).toBe("lime");
    expect(readDesktopTheme({ getItem: () => "unknown-theme" })).toBe("lime");
  });

  it("persists and restores a supported dopamine palette", () => {
    const values = new Map<string, string>();
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    };

    persistDesktopTheme("berry", storage);
    expect(readDesktopTheme(storage)).toBe("berry");
  });

  it("materializes multi-accent semantic variables and the theme data attribute", () => {
    const properties = new Map<string, string>();
    const root = {
      style: { setProperty: (name: string, value: string) => properties.set(name, value) },
      dataset: {},
    } as unknown as HTMLElement;

    applyDesktopTheme(root, "tangerine");

    expect(root.dataset.fsiTheme).toBe("tangerine");
    expect(properties.get("--fsi-color-accent")).toBe("#ff5e37");
    expect(properties.get("--fsi-color-ambient-1")).toBe("#ffd43b");
    expect(properties.get("--fsi-color-ambient-2")).toBe("#24bfd4");
    expect(properties.get("--fsi-color-hero-background")).toContain("linear-gradient");
    expect(properties.get("--fsi-spectrum-7")).toBe("#5568ff");
  });
});
