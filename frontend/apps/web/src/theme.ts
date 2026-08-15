import { designTokens } from "@family-spending/design-tokens";

export const desktopThemeOptions = [
  {
    id: "lime",
    label: "酸柠",
    description: "青柠 · 钴蓝 · 电青",
    swatches: ["#dfff3f", "#5b4fff", "#66e6ff"],
  },
  {
    id: "berry",
    label: "莓果",
    description: "玫红 · 紫罗兰 · 珊瑚",
    swatches: ["#ff4fa3", "#8b5cf6", "#ff7a4d"],
  },
  {
    id: "tangerine",
    label: "橘浪",
    description: "橘子 · 柠黄 · 海蓝",
    swatches: ["#ff7a2f", "#ffd43b", "#24bfd4"],
  },
  {
    id: "grape",
    label: "葡萄",
    description: "葡萄 · 电青 · 桃粉",
    swatches: ["#7b4dff", "#00c8ff", "#ff5fb2"],
  },
] as const;

export type DesktopThemeId = (typeof desktopThemeOptions)[number]["id"];

interface DesktopThemePalette {
  canvas: string;
  surface: string;
  surfaceSubtle: string;
  surfaceHover: string;
  text: string;
  textMuted: string;
  textSubtle: string;
  textFaint: string;
  border: string;
  borderStrong: string;
  accent: string;
  accentHover: string;
  accentSubtle: string;
  ambient1: string;
  ambient2: string;
  positive: string;
  negative: string;
  negativeSubtle: string;
  warning: string;
  warningSubtle: string;
  header: string;
  heroBackground: string;
  heroForeground: string;
  heroMuted: string;
  heroFaint: string;
  heroLine: string;
  heroGrid: string;
  heroAmbient: string;
  heroAmbientSoft: string;
  heroAmbientFaint: string;
  heroNegative: string;
  chartGrid: string;
  chartArea: string;
  chartPoint: string;
  spectrum: readonly [string, string, string, string, string, string, string];
  cardShadow: string;
  heroShadow: string;
  overlayShadow: string;
}

const themePalettes: Record<DesktopThemeId, DesktopThemePalette> = {
  lime: {
    canvas: "#f9ffef",
    surface: "#ffffff",
    surfaceSubtle: "#eff8e6",
    surfaceHover: "#e9f4df",
    text: "#171b24",
    textMuted: "#697063",
    textSubtle: "#929989",
    textFaint: "#c2cab9",
    border: "#dfe8d6",
    borderStrong: "#c3cfb8",
    accent: "#5b4fff",
    accentHover: "#4639ea",
    accentSubtle: "#eeebff",
    ambient1: "#dfff3f",
    ambient2: "#66e6ff",
    positive: "#13a55f",
    negative: "#e34867",
    negativeSubtle: "#fff0f4",
    warning: "#d88900",
    warningSubtle: "#fff5d3",
    header: "rgba(249, 255, 239, 0.88)",
    heroBackground: "linear-gradient(135deg, #efff55 0%, #b8ff67 42%, #66e6ff 100%)",
    heroForeground: "#18213a",
    heroMuted: "rgba(24, 33, 58, 0.66)",
    heroFaint: "rgba(24, 33, 58, 0.38)",
    heroLine: "rgba(24, 33, 58, 0.14)",
    heroGrid: "rgba(24, 33, 58, 0.045)",
    heroAmbient: "rgba(91, 79, 255, 0.22)",
    heroAmbientSoft: "rgba(91, 79, 255, 0.055)",
    heroAmbientFaint: "rgba(91, 79, 255, 0.02)",
    heroNegative: "#c91f4b",
    chartGrid: "#e6eedf",
    chartArea: "rgba(91, 79, 255, 0.10)",
    chartPoint: "#5b4fff",
    spectrum: ["#5b4fff", "#ff4fa3", "#ff7a33", "#ffc928", "#9bd72f", "#14c8b8", "#35a7ff"],
    cardShadow: "0 16px 42px rgba(92, 108, 68, 0.08)",
    heroShadow: "0 28px 72px rgba(108, 139, 70, 0.20), 0 2px 0 rgba(255, 255, 255, 0.28) inset",
    overlayShadow: "0 16px 36px rgba(61, 70, 49, 0.18)",
  },
  berry: {
    canvas: "#fff5fb",
    surface: "#ffffff",
    surfaceSubtle: "#fcebf5",
    surfaceHover: "#f8e4f1",
    text: "#251726",
    textMuted: "#786575",
    textSubtle: "#a28d9d",
    textFaint: "#d0bfca",
    border: "#eedce8",
    borderStrong: "#d9bfd0",
    accent: "#e83e9b",
    accentHover: "#ca2c84",
    accentSubtle: "#ffe8f4",
    ambient1: "#ff4fa3",
    ambient2: "#8b5cf6",
    positive: "#14a879",
    negative: "#df375a",
    negativeSubtle: "#ffedf2",
    warning: "#dc8a00",
    warningSubtle: "#fff2cf",
    header: "rgba(255, 245, 251, 0.88)",
    heroBackground: "linear-gradient(135deg, #ff71c5 0%, #d65dff 48%, #695cff 100%)",
    heroForeground: "#ffffff",
    heroMuted: "rgba(255, 255, 255, 0.72)",
    heroFaint: "rgba(255, 255, 255, 0.40)",
    heroLine: "rgba(255, 255, 255, 0.20)",
    heroGrid: "rgba(255, 255, 255, 0.055)",
    heroAmbient: "rgba(255, 226, 103, 0.24)",
    heroAmbientSoft: "rgba(255, 226, 103, 0.07)",
    heroAmbientFaint: "rgba(255, 226, 103, 0.025)",
    heroNegative: "#ffe465",
    chartGrid: "#f0e2eb",
    chartArea: "rgba(232, 62, 155, 0.10)",
    chartPoint: "#e83e9b",
    spectrum: ["#ff4fa3", "#9a5cff", "#5d6bff", "#24b8ff", "#23d6b5", "#ffd43b", "#ff7a4d"],
    cardShadow: "0 16px 42px rgba(116, 61, 95, 0.08)",
    heroShadow: "0 28px 74px rgba(132, 56, 153, 0.24), 0 2px 0 rgba(255, 255, 255, 0.20) inset",
    overlayShadow: "0 16px 36px rgba(78, 40, 70, 0.20)",
  },
  tangerine: {
    canvas: "#fff9ee",
    surface: "#ffffff",
    surfaceSubtle: "#fff0dd",
    surfaceHover: "#fbe8d1",
    text: "#2b1c1a",
    textMuted: "#7c6b62",
    textSubtle: "#a58f83",
    textFaint: "#d0beb2",
    border: "#eedfce",
    borderStrong: "#dbc3ad",
    accent: "#ff5e37",
    accentHover: "#e44725",
    accentSubtle: "#ffe9df",
    ambient1: "#ffd43b",
    ambient2: "#24bfd4",
    positive: "#139b6e",
    negative: "#d93659",
    negativeSubtle: "#ffedf1",
    warning: "#c77c00",
    warningSubtle: "#fff0c6",
    header: "rgba(255, 249, 238, 0.89)",
    heroBackground: "linear-gradient(135deg, #ffd84a 0%, #ff8b35 52%, #ff4e86 100%)",
    heroForeground: "#2d1820",
    heroMuted: "rgba(45, 24, 32, 0.66)",
    heroFaint: "rgba(45, 24, 32, 0.38)",
    heroLine: "rgba(45, 24, 32, 0.14)",
    heroGrid: "rgba(45, 24, 32, 0.045)",
    heroAmbient: "rgba(36, 191, 212, 0.25)",
    heroAmbientSoft: "rgba(36, 191, 212, 0.07)",
    heroAmbientFaint: "rgba(36, 191, 212, 0.025)",
    heroNegative: "#8b1e3f",
    chartGrid: "#f0e4d6",
    chartArea: "rgba(255, 94, 55, 0.10)",
    chartPoint: "#ff5e37",
    spectrum: ["#ff5e37", "#ff9d2e", "#ffd43b", "#a9dd3b", "#24c9a9", "#24bfd4", "#5568ff"],
    cardShadow: "0 16px 42px rgba(122, 84, 49, 0.08)",
    heroShadow: "0 28px 74px rgba(204, 99, 48, 0.23), 0 2px 0 rgba(255, 255, 255, 0.22) inset",
    overlayShadow: "0 16px 36px rgba(84, 55, 37, 0.20)",
  },
  grape: {
    canvas: "#f8f5ff",
    surface: "#ffffff",
    surfaceSubtle: "#efeaff",
    surfaceHover: "#e9e2fb",
    text: "#20182d",
    textMuted: "#70667c",
    textSubtle: "#958aa3",
    textFaint: "#c6bbd1",
    border: "#e3dbed",
    borderStrong: "#cdbedd",
    accent: "#7b4dff",
    accentHover: "#6537e8",
    accentSubtle: "#eee8ff",
    ambient1: "#ff5fb2",
    ambient2: "#00c8ff",
    positive: "#10a579",
    negative: "#de3c69",
    negativeSubtle: "#ffedf4",
    warning: "#d58b00",
    warningSubtle: "#fff3cd",
    header: "rgba(248, 245, 255, 0.89)",
    heroBackground: "linear-gradient(135deg, #5a38db 0%, #8a4eff 48%, #00c8ff 112%)",
    heroForeground: "#ffffff",
    heroMuted: "rgba(255, 255, 255, 0.72)",
    heroFaint: "rgba(255, 255, 255, 0.40)",
    heroLine: "rgba(255, 255, 255, 0.20)",
    heroGrid: "rgba(255, 255, 255, 0.055)",
    heroAmbient: "rgba(255, 95, 178, 0.25)",
    heroAmbientSoft: "rgba(255, 95, 178, 0.07)",
    heroAmbientFaint: "rgba(255, 95, 178, 0.025)",
    heroNegative: "#ffe15c",
    chartGrid: "#ebe4f4",
    chartArea: "rgba(123, 77, 255, 0.10)",
    chartPoint: "#7b4dff",
    spectrum: ["#7b4dff", "#b448ff", "#ff5fb2", "#ff7d5c", "#ffd43b", "#2fd3b0", "#00c8ff"],
    cardShadow: "0 16px 42px rgba(75, 54, 112, 0.08)",
    heroShadow: "0 28px 74px rgba(86, 53, 170, 0.25), 0 2px 0 rgba(255, 255, 255, 0.18) inset",
    overlayShadow: "0 16px 36px rgba(55, 39, 78, 0.20)",
  },
};

const DESKTOP_THEME_STORAGE_KEY = "family-spending.desktop-theme";
const DEFAULT_DESKTOP_THEME: DesktopThemeId = "lime";

/** Narrow arbitrary persisted values to one of the supported desktop tone themes. */
function isDesktopThemeId(value: string | null): value is DesktopThemeId {
  return desktopThemeOptions.some((option) => option.id === value);
}

/** Access localStorage defensively because browser privacy modes can deny it. */
function defaultStorage(): Pick<Storage, "getItem" | "setItem"> | null {
  if (typeof window === "undefined") return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

/** Read the persisted desktop tone, falling back to the dopamine Lime palette. */
export function readDesktopTheme(
  storage: Pick<Storage, "getItem"> | null = defaultStorage(),
): DesktopThemeId {
  if (storage === null) return DEFAULT_DESKTOP_THEME;
  try {
    const stored = storage.getItem(DESKTOP_THEME_STORAGE_KEY);
    return isDesktopThemeId(stored) ? stored : DEFAULT_DESKTOP_THEME;
  } catch {
    return DEFAULT_DESKTOP_THEME;
  }
}

/** Persist a user-selected desktop tone without making storage availability a product dependency. */
export function persistDesktopTheme(
  themeId: DesktopThemeId,
  storage: Pick<Storage, "setItem"> | null = defaultStorage(),
): void {
  if (storage === null) return;
  try {
    storage.setItem(DESKTOP_THEME_STORAGE_KEY, themeId);
  } catch {
    // Theme switching must still work for the current session when persistence is unavailable.
  }
}

/** Materialize one semantic desktop palette as CSS variables for the renderer. */
export function applyDesktopTheme(
  root: HTMLElement,
  themeId: DesktopThemeId = readDesktopTheme(),
): void {
  const { density, radius, spacing, typography } = designTokens;
  const palette = themePalettes[themeId];
  const variables: Record<string, string> = {
    "--fsi-color-canvas": palette.canvas,
    "--fsi-color-surface": palette.surface,
    "--fsi-color-surface-subtle": palette.surfaceSubtle,
    "--fsi-color-surface-hover": palette.surfaceHover,
    "--fsi-color-text": palette.text,
    "--fsi-color-text-muted": palette.textMuted,
    "--fsi-color-text-subtle": palette.textSubtle,
    "--fsi-color-text-faint": palette.textFaint,
    "--fsi-color-border": palette.border,
    "--fsi-color-border-strong": palette.borderStrong,
    "--fsi-color-accent": palette.accent,
    "--fsi-color-accent-hover": palette.accentHover,
    "--fsi-color-accent-subtle": palette.accentSubtle,
    "--fsi-color-ambient-1": palette.ambient1,
    "--fsi-color-ambient-2": palette.ambient2,
    "--fsi-color-positive": palette.positive,
    "--fsi-color-negative": palette.negative,
    "--fsi-color-negative-subtle": palette.negativeSubtle,
    "--fsi-color-warning": palette.warning,
    "--fsi-color-warning-subtle": palette.warningSubtle,
    "--fsi-color-header": palette.header,
    "--fsi-color-hero-background": palette.heroBackground,
    "--fsi-color-hero-foreground": palette.heroForeground,
    "--fsi-color-hero-muted": palette.heroMuted,
    "--fsi-color-hero-faint": palette.heroFaint,
    "--fsi-color-hero-line": palette.heroLine,
    "--fsi-color-hero-grid": palette.heroGrid,
    "--fsi-color-hero-ambient": palette.heroAmbient,
    "--fsi-color-hero-ambient-soft": palette.heroAmbientSoft,
    "--fsi-color-hero-ambient-faint": palette.heroAmbientFaint,
    "--fsi-color-hero-negative": palette.heroNegative,
    "--fsi-color-chart-grid": palette.chartGrid,
    "--fsi-color-chart-area": palette.chartArea,
    "--fsi-color-chart-point": palette.chartPoint,
    "--fsi-spectrum-1": palette.spectrum[0],
    "--fsi-spectrum-2": palette.spectrum[1],
    "--fsi-spectrum-3": palette.spectrum[2],
    "--fsi-spectrum-4": palette.spectrum[3],
    "--fsi-spectrum-5": palette.spectrum[4],
    "--fsi-spectrum-6": palette.spectrum[5],
    "--fsi-spectrum-7": palette.spectrum[6],
    "--fsi-shadow-card": palette.cardShadow,
    "--fsi-shadow-hero": palette.heroShadow,
    "--fsi-shadow-overlay": palette.overlayShadow,
    "--fsi-sidebar-width": `${density.desktop.sidebarWidth}px`,
    "--fsi-control-height": `${density.desktop.controlHeight}px`,
    "--fsi-nav-row-height": `${density.desktop.navigationRowHeight}px`,
    "--fsi-radius-small": `${radius.small}px`,
    "--fsi-radius-medium": `${radius.medium}px`,
    "--fsi-radius-large": `${radius.large}px`,
    "--fsi-space-1": `${spacing[1]}px`,
    "--fsi-space-2": `${spacing[2]}px`,
    "--fsi-space-3": `${spacing[3]}px`,
    "--fsi-space-4": `${spacing[4]}px`,
    "--fsi-space-6": `${spacing[6]}px`,
    "--fsi-space-8": `${spacing[8]}px`,
    "--fsi-space-10": `${spacing[10]}px`,
    "--fsi-font-page-title": `${typography.pageTitle.size}px`,
    "--fsi-font-section-title": `${typography.sectionTitle.size}px`,
    "--fsi-font-body": `${typography.body.size}px`,
    "--fsi-font-secondary": `${typography.secondary.size}px`,
    "--fsi-font-meta": `${typography.meta.size}px`,
    "--fsi-font-hero": `${typography.heroAmount.size}px`,
  };

  for (const [name, value] of Object.entries(variables)) {
    root.style.setProperty(name, value);
  }
  root.dataset.fsiTheme = themeId;
}
