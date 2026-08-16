export const miniThemeOptions = [
  {
    id: "lime",
    label: "酸柠",
    description: "青柠 · 钴蓝 · 电青",
    swatchA: "#dfff3f",
    swatchB: "#5b4fff",
    swatchC: "#66e6ff",
  },
  {
    id: "berry",
    label: "莓果",
    description: "玫红 · 紫罗兰 · 珊瑚",
    swatchA: "#ff4fa3",
    swatchB: "#8b5cf6",
    swatchC: "#ff7a4d",
  },
  {
    id: "tangerine",
    label: "橘浪",
    description: "橘子 · 柠黄 · 海蓝",
    swatchA: "#ff7a2f",
    swatchB: "#ffd43b",
    swatchC: "#24bfd4",
  },
  {
    id: "grape",
    label: "葡萄",
    description: "葡萄 · 电青 · 桃粉",
    swatchA: "#7b4dff",
    swatchB: "#00c8ff",
    swatchC: "#ff5fb2",
  },
] as const;

export type MiniThemeId = (typeof miniThemeOptions)[number]["id"];

interface MiniThemePalette {
  canvas: string;
  textMuted: string;
  accent: string;
  surface: string;
}

interface MiniThemePlatform {
  getStorageSync(key: string): unknown;
  setStorageSync(key: string, value: unknown): void;
  setNavigationBarColor(options: {
    frontColor: "#000000" | "#ffffff";
    backgroundColor: string;
  }): unknown;
  setTabBarStyle(options: {
    color: string;
    selectedColor: string;
    backgroundColor: string;
    borderStyle: "black" | "white";
  }): unknown;
}

const MINI_THEME_STORAGE_KEY = "family-spending.mini-theme";
const DEFAULT_MINI_THEME: MiniThemeId = "lime";

const themeChrome: Record<MiniThemeId, MiniThemePalette> = {
  lime: {
    canvas: "#f9ffef",
    textMuted: "#697063",
    accent: "#5b4fff",
    surface: "#ffffff",
  },
  berry: {
    canvas: "#fff5fb",
    textMuted: "#786575",
    accent: "#e83e9b",
    surface: "#ffffff",
  },
  tangerine: {
    canvas: "#fff9ee",
    textMuted: "#7c6b62",
    accent: "#ff5e37",
    surface: "#ffffff",
  },
  grape: {
    canvas: "#f8f5ff",
    textMuted: "#70667c",
    accent: "#7b4dff",
    surface: "#ffffff",
  },
};

export function isMiniThemeId(value: unknown): value is MiniThemeId {
  return (
    typeof value === "string" &&
    miniThemeOptions.some((option) => option.id === value)
  );
}

export function readMiniTheme(
  platform: Pick<MiniThemePlatform, "getStorageSync"> = wx,
): MiniThemeId {
  try {
    const value = platform.getStorageSync(MINI_THEME_STORAGE_KEY);
    return isMiniThemeId(value) ? value : DEFAULT_MINI_THEME;
  } catch {
    return DEFAULT_MINI_THEME;
  }
}

export function persistMiniTheme(
  themeId: MiniThemeId,
  platform: Pick<MiniThemePlatform, "setStorageSync"> = wx,
): void {
  try {
    platform.setStorageSync(MINI_THEME_STORAGE_KEY, themeId);
  } catch {
    // Theme switching still works for the current page when storage is unavailable.
  }
}

export function applyMiniThemeChrome(
  themeId: MiniThemeId,
  platform: Pick<MiniThemePlatform, "setNavigationBarColor" | "setTabBarStyle"> = wx,
): void {
  const palette = themeChrome[themeId];
  try {
    platform.setNavigationBarColor({
      frontColor: "#000000",
      backgroundColor: palette.canvas,
    });
    platform.setTabBarStyle({
      color: palette.textMuted,
      selectedColor: palette.accent,
      backgroundColor: palette.surface,
      borderStyle: "white",
    });
  } catch {
    // Page-level theme classes remain authoritative even if native chrome APIs fail.
  }
}
