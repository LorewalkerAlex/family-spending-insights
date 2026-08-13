import { designTokens } from "@family-spending/design-tokens";

/** Materialize shared design tokens as CSS variables for the Desktop Web renderer. */
export function applyDesktopTheme(root: HTMLElement): void {
  const { colors, density, overlayShadow, radius, spacing, typography } = designTokens;
  const variables: Record<string, string> = {
    "--fsi-color-canvas": colors.canvas,
    "--fsi-color-surface": colors.surface,
    "--fsi-color-text": colors.text,
    "--fsi-color-text-muted": colors.textMuted,
    "--fsi-color-border": colors.border,
    "--fsi-color-accent": colors.accent,
    "--fsi-color-accent-subtle": colors.accentSubtle,
    "--fsi-color-positive": colors.positive,
    "--fsi-color-negative": colors.negative,
    "--fsi-color-warning": colors.warning,
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
    "--fsi-shadow-overlay": overlayShadow,
  };

  for (const [name, value] of Object.entries(variables)) {
    root.style.setProperty(name, value);
  }
}
