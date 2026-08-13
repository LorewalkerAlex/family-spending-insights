export const colors = {
  canvas: "#f7f8f6",
  surface: "#ffffff",
  text: "#1d211e",
  textMuted: "#687169",
  border: "#dce2dc",
  accent: "#2f6b4f",
  accentSubtle: "#e9f1ec",
  positive: "#2f6b4f",
  negative: "#a33d3d",
  warning: "#9a6a1f",
} as const;

export const spacing = {
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  6: 24,
  8: 32,
  10: 40,
} as const;

export const radius = {
  small: 4,
  medium: 6,
  large: 8,
} as const;

export const typography = {
  pageTitle: { size: 26, weight: 600 },
  sectionTitle: { size: 17, weight: 600 },
  body: { size: 14, weight: 400 },
  secondary: { size: 13, weight: 400 },
  meta: { size: 12, weight: 400 },
  heroAmount: { size: 36, weight: 600 },
} as const;

export const density = {
  desktop: {
    sidebarWidth: 232,
    navigationRowHeight: 34,
    controlHeight: 36,
  },
  mini: {
    horizontalPadding: 16,
    minimumTouchTarget: 44,
  },
} as const;

export const overlayShadow = "0 12px 32px rgba(29, 33, 30, 0.14)";

export const designTokens = {
  colors,
  spacing,
  radius,
  typography,
  density,
  overlayShadow,
} as const;
