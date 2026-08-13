import { designTokens } from "@family-spending/design-tokens";

/** Mini consumes the same semantic tokens as Desktop while keeping touch density platform-specific. */
export const miniTheme = {
  ...designTokens,
  pagePadding: designTokens.density.mini.horizontalPadding,
  minimumTouchTarget: designTokens.density.mini.minimumTouchTarget,
} as const;
